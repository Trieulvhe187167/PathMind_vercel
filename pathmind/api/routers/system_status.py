from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.services import system_status

router = APIRouter()  # mounted at /api/system-status


@router.get("")
def status(db: Session = Depends(get_db)):
    return system_status.collect(db)
