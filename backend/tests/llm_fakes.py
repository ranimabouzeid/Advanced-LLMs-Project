"""Offline structured decisions; trusted costs and findings come from production tools."""

import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from app.graph.state import FleetExplanation, OrderSelection, SafetyDecision


class Fake(FakeMessagesListChatModel):
    auto_select: bool = False
    scripts: dict[str, list[dict]] = Field(default_factory=dict)
    calls: list = Field(default_factory=list)

    def with_structured_output(self, schema, *, method=None, **kwargs):
        assert method == "function_calling"
        def invoke(messages):
            payload = json.loads(messages[-1].content)
            self.calls.append((schema, payload, messages[0].content))
            if schema.__name__ in self.scripts and self.scripts[schema.__name__]:
                return schema.model_validate(self.scripts[schema.__name__].pop(0))
            if not self.auto_select:
                return schema.model_validate_json(self.invoke(messages).content)
            return automatic(schema, payload)
        return RunnableLambda(invoke)


def client(*outputs, scripts=None):
    return Fake(auto_select=not outputs, scripts=scripts or {},
                responses=[AIMessage(content=json.dumps(output)) for output in outputs] or [AIMessage(content="unused")])


def automatic(schema, data):
    if schema is OrderSelection:
        return schema(order_id=data["pending_orders"][0]["id"], explanation="Mock selects oldest")
    if schema is FleetExplanation:
        return schema(explanation=f"Explaining deterministic assignment {data['selected_robot_id']} from supplied costs")
    if schema is SafetyDecision:
        facts = data["trusted_findings"]
        approved = facts["route_valid"]
        return schema(approved=approved, conflicts=[] if approved else ["Correct the trusted hard findings"],
                      explanation="Mock interprets trusted findings")
    raise AssertionError(f"Unexpected schema: {schema}")
