from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.user import Profile, ProfileUpdate

router = APIRouter(tags=["me"])


@router.get("/me", response_model=Profile)
async def get_me(
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> Profile:
    row = await conn.fetchrow(
        "select id, full_name, preferences, created_at, updated_at from public.profiles where id = $1",
        user_id,
    )
    if row is None:
        # The signup trigger creates this row; missing means the account is in a bad state.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return Profile(**dict(row))


@router.patch("/me", response_model=Profile)
async def update_me(
    payload: ProfileUpdate,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> Profile:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        row = await conn.fetchrow(
            "select id, full_name, preferences, created_at, updated_at from public.profiles where id = $1",
            user_id,
        )
    else:
        # Build the SET clause from only the fields the client actually sent, still scoped to user_id
        # taken from the verified JWT above -- never from the request body (CLAUDE.md rule 13).
        set_clauses = []
        values: list = []
        for i, (key, value) in enumerate(fields.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            values.append(value)
        values.append(user_id)

        query = f"""
            update public.profiles
            set {", ".join(set_clauses)}
            where id = ${len(values)}
            returning id, full_name, preferences, created_at, updated_at
        """
        row = await conn.fetchrow(query, *values)

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return Profile(**dict(row))
