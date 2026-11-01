from typing import Any
from fastapi import HTTPException
from app.db import SessionLocal
from app.services import glm, store
from app.services.catalog import CATALOG
from app.services.change_sets import ChangeSetError, apply_items, normalize_operations

# Design assistant (ai_assistant schema): a chat message gets a reply plus a proposed change set of Puck operations
# on one page. Applying a change set edits that page (project.pages) and bumps the project's edit_revision; a change
# set made against an older edit_revision is rejected instead of overwriting newer edits.

def send_message(conversation_id, *, content: str, page_id=None) -> dict[str, Any]:
    if not glm.is_configured():  # before saving the message, so an unanswerable one is not stored
        raise HTTPException(status_code=503, detail="The design assistant is unavailable: GLM_BASE_URL and GLM_API_KEY are not set.")
    with SessionLocal() as db:
        conversation = store.get_conversation(db, conversation_id, lock=True)
        project = store.get_project(db, conversation["project_id"])
        page = store.get_page(db, project["project_id"], page_id) if page_id else store.home_page(db, project["project_id"])
        history = [{"role": m["role"].lower(), "content": m["content"]} for m in store.list_messages(db, conversation_id)]
        user_message = store.add_message(db, conversation_id, role="USER", content=content, page_id=page["page_id"])
        db.commit()

    # The model call runs outside any transaction; it can take a minute on a local model.
    try:
        proposal = glm.propose_change_set(page, CATALOG, history, content)
    except glm.GLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    with SessionLocal() as db:
        warning = None
        items: list[dict[str, Any]] = []
        try:
            items = normalize_operations(proposal["operations"], page["puck_tree"],
                                         store.component_definitions(db, project["component_catalog_version"]))
            apply_items(page["puck_tree"], items)  # dry run: the result must be a valid page
        except ChangeSetError as e:
            items, warning = [], f"The assistant proposed an invalid change, so nothing was proposed: {e}"
        store.get_conversation(db, conversation_id, lock=True)
        reply = proposal["reply"] + (f"\n\n({warning})" if warning else "")
        assistant_message = store.add_message(db, conversation_id, role="ASSISTANT", content=reply,
                                              page_id=page["page_id"], usage=proposal["usage"])
        change_set_id = None
        if items:
            change_set_id = store.create_change_set(db, conversation_id=conversation_id, message_id=assistant_message["message_id"],
                                                    project_id=project["project_id"], page_id=page["page_id"],
                                                    catalog_version=project["component_catalog_version"],
                                                    base_edit_revision=project["edit_revision"], items=items)
        db.commit()
        return {"user_message": user_message, "assistant_message": assistant_message, "warning": warning,
                "change_set": store.get_change_set(db, change_set_id) if change_set_id else None}

def _reject(db, change_set_id, code: str, status_code: int, detail: str) -> HTTPException:
    store.decide_change_set(db, change_set_id, "REJECTED", rejection_code=code)
    db.commit()
    return HTTPException(status_code=status_code, detail=detail)

def apply_change_set(change_set_id) -> dict[str, Any]:
    with SessionLocal() as db:
        change_set = store.get_change_set(db, change_set_id, lock=True)
        if change_set["status"] != "PROPOSED":
            raise HTTPException(status_code=409, detail=f"Change set is already {change_set['status'].lower()}.")
        project = store.get_project(db, change_set["project_id"], lock=True)
        if project["edit_revision"] != change_set["base_edit_revision"]:
            raise _reject(db, change_set_id, "STALE_EDIT_REVISION", 409,
                          "The page changed after this change set was proposed; ask the assistant again.")
        try:
            page = store.get_page(db, project["project_id"], change_set["page_id"])
        except HTTPException:
            raise _reject(db, change_set_id, "PAGE_NOT_FOUND", 409, "The page no longer exists.") from None
        try:
            tree = apply_items(page["puck_tree"], change_set["items"])
        except ChangeSetError as e:
            raise _reject(db, change_set_id, "INVALID_RESULT", 422, str(e)) from e
        page = store.update_page(db, project["project_id"], page["page_id"], {"puck_tree": tree})
        revision = store.bump_edit_revision(db, project["project_id"])
        store.decide_change_set(db, change_set_id, "APPLIED", applied_edit_revision=revision)
        db.commit()
        return {"change_set": store.get_change_set(db, change_set_id), "page": page, "edit_revision": revision}

def discard_change_set(change_set_id) -> dict[str, Any]:
    with SessionLocal() as db:
        change_set = store.get_change_set(db, change_set_id, lock=True)
        if change_set["status"] != "PROPOSED":
            raise HTTPException(status_code=409, detail=f"Change set is already {change_set['status'].lower()}.")
        store.decide_change_set(db, change_set_id, "DISCARDED")
        db.commit()
        return store.get_change_set(db, change_set_id)
