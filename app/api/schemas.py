from pydantic import BaseModel, Field


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

class VerifyIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    demo_pin: str = Field(min_length=1, max_length=32)

class ConversationOut(BaseModel):
    conversation_id: str
    status: str
    workflow_state: str | None = None
    message: str
    required_input: str | None = None
    handoff_ticket_id: str | None = None
    intent: str | None = None
