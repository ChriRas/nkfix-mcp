"""Approval: when a record tool may save despite concerns.

If there are concerns, the human decides, not the model:

- If the client can ask (MCP elicitation, e.g. Claude Code), the server asks
  the human directly. Their 'no' is final, even if the model sends an approval
  code along.
- Otherwise "not saved" comes back with the concerns and an approval code.
  The model must ask the human and, only with their consent, repeat the same
  call with ``approval_code=<code>``. The code matches exactly these arguments
  (for a receipt also its content) and these concerns (review S4-2): it cannot
  be set in advance, and if a value changes or a concern is added before the
  second call, it no longer applies. Even the code does not prove that the
  human was actually asked.
"""

import hmac
import json
import secrets
from typing import Annotated, Any

from mcp.server.mcpserver import Context, Elicit, ElicitationResult
from pydantic import BaseModel, Field

ApprovalCode = Annotated[
    str | None,
    Field(
        max_length=20,
        description=(
            'Freigabecode aus einer vorigen Antwort mit saved=false. Nur mitschicken, nachdem du dem Menschen die '
            'Bedenken gezeigt hast und er dem Speichern ausdrücklich zugestimmt hat. Nie vorab und nie von dir aus.'
        ),
    ),
]

# New on every start: a code is only valid in this process and cannot be
# computed in advance from the source.
_SALT = secrets.token_bytes(16)


def _approval_code(values: tuple[Any, ...], concerns: list[str]) -> str:
    raw = json.dumps([values, concerns], default=str, ensure_ascii=False)
    return hmac.new(_SALT, raw.encode(), 'sha256').hexdigest()[:8]


class ConfirmationPrompt(BaseModel):
    save: bool = Field(title='Trotzdem speichern?', description='Ja: der Eintrag wird trotzdem angelegt.')


class PrecheckResult(BaseModel):
    """Result of the precheck when nobody was asked."""

    concerns: list[str]
    can_elicit: bool
    approval_code: str | None = None


class RecordResult(BaseModel):
    saved: bool
    id: int | None = Field(description='ID des neuen Eintrags')
    concerns: list[str] = Field(description='was gegen das Speichern sprach (auch wenn trotzdem gespeichert wurde)')
    note: str
    approval_code: str | None = Field(
        default=None, description=('Code für den zweiten Aufruf, falls der Mensch trotz der Bedenken speichern will')
    )


class Decision(BaseModel):
    """Save: the human agreed, or there was nothing to agree to.

    Not saving is no Decision but the ``RecordResult`` that goes back to the model.
    """

    despite_concerns: bool = False
    concerns: list[str] = []


def _can_elicit(ctx: Context) -> bool:
    capabilities = ctx.client_capabilities
    elicitation = capabilities.elicitation if capabilities is not None else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def ask(
    ctx: Context, summary: str, concerns: list[str], values: tuple[Any, ...]
) -> Elicit[ConfirmationPrompt] | PrecheckResult:
    """``values``: the tool and all arguments the approval code is bound to."""
    if not concerns:
        return PrecheckResult(concerns=[], can_elicit=True)
    if not _can_elicit(ctx):
        return PrecheckResult(concerns=concerns, can_elicit=False, approval_code=_approval_code(values, concerns))
    bullets = '\n'.join(f'• {b}' for b in concerns)
    return Elicit(f'{summary}\n\nBedenken:\n{bullets}\n\nTrotzdem speichern?', ConfirmationPrompt)


def decide(decision: ElicitationResult[ConfirmationPrompt], approval_code: str | None) -> Decision | RecordResult:
    if decision.action != 'accept':
        return RecordResult(
            saved=False,
            id=None,
            concerns=[],
            note='Nicht gespeichert: Der Mensch hat abgelehnt oder abgebrochen. Er kann den Eintrag selbst in '
            'NebenkostenFix anlegen.',
        )
    data = decision.data
    if isinstance(data, ConfirmationPrompt):
        if data.save:
            return Decision(despite_concerns=True)
        return RecordResult(saved=False, id=None, concerns=[], note='Nicht gespeichert: Der Mensch hat abgelehnt.')
    if not data.concerns:
        return Decision()
    # As bytes: compare_digest raises on str with umlauts ("bestätigt").
    if approval_code is not None and hmac.compare_digest(
        approval_code.strip().lower().encode(), data.approval_code.encode()
    ):
        return Decision(despite_concerns=True, concerns=data.concerns)
    note = (
        f'Zeig dem Menschen die Bedenken und frag, ob trotzdem gespeichert werden soll. Nur wenn er '
        f"zustimmt: dasselbe Werkzeug mit denselben Werten und approval_code='{data.approval_code}' erneut "
        f'aufrufen.'
    )
    if approval_code is not None:
        note = (
            'Nicht gespeichert: Der Freigabecode passt nicht (mehr). Er gilt nur für die Werte und Bedenken '
            'der Antwort, aus der er stammt; die haben sich geändert, der Server wurde neu gestartet oder '
            f'der Code ist geraten. {note}'
        )
    else:
        note = f'Nicht gespeichert. {note}'
    return RecordResult(saved=False, id=None, concerns=data.concerns, note=note, approval_code=data.approval_code)


def saved(response: Any, decision: Decision) -> RecordResult:
    new_id = response.get('id') if isinstance(response, dict) else None
    if decision.concerns:
        # Via the approval code: the concerns belong in the response so that the
        # human sees them, even if the model did not pass them on.
        note = 'Gespeichert trotz Bedenken (Freigabecode). Nenne dem Menschen die Bedenken.'
    elif decision.despite_concerns:
        note = 'Gespeichert, nachdem der Mensch im Dialog zugestimmt hat.'
    else:
        note = 'Gespeichert.'
    return RecordResult(saved=True, id=new_id, concerns=decision.concerns, note=note)
