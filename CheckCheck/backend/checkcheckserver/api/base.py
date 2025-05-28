from pydantic import BaseModel


class HTTPErrorResponeRepresentation(BaseModel):
    # this is just a placeholder type for API docs
    # https://stackoverflow.com/a/64505982/12438690
    detail: str

    class Config:
        json_schema_extra = {
            "example": {"detail": "HTTPException raised."},
        }


class HTTPMessage(BaseModel):
    message: str
