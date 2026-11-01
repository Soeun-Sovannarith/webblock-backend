from uuid import UUID
from fastapi import APIRouter, Response
from app.db import SessionLocal
from app.schemas.harness import ChangeSetUpdate, ConversationCreate, MessageCreate
from app.services import assistant, store
router = APIRouter()

@router.post("/projects/{project_id}/conversations", status_code=201)
def create_conversation(project_id: UUID, payload: ConversationCreate, response: Response):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        conversation = store.create_conversation(db, project_id, title=payload.title)
        db.commit()
        response.headers["Location"] = f"/v1/conversations/{conversation['conversation_id']}"
        return conversation

@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID):
    with SessionLocal() as db:
        conversation = store.get_conversation(db, conversation_id)
        return {**conversation, "messages": store.list_messages(db, conversation_id),
                "change_sets": store.list_change_sets(db, conversation_id)}

@router.post("/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: UUID, payload: MessageCreate):
    """Send a chat message; the reply comes with the assistant's proposed change set (or none)."""
    return assistant.send_message(conversation_id, content=payload.content, page_id=payload.page_id)

@router.get("/change-sets/{change_set_id}", summary="Get Change Set")
def get_change_set(change_set_id: UUID):
    with SessionLocal() as db:
        return store.get_change_set(db, change_set_id)

@router.patch("/change-sets/{change_set_id}", summary="Apply Or Discard Change Set")
def update_change_set(change_set_id: UUID, payload: ChangeSetUpdate):
    """Decide a PROPOSED change set: APPLIED answers {change_set, page, edit_revision}, DISCARDED {change_set}."""
    if payload.status == "APPLIED":
        return assistant.apply_change_set(change_set_id)
    return {"change_set": assistant.discard_change_set(change_set_id)}
