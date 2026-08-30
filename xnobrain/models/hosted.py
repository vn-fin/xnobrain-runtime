from pydantic import BaseModel,Field
class HostedBootstrapRequest(BaseModel):
 installation_id:str
 package:dict
class HostedExecuteRequest(BaseModel):
 instruction:str=Field(min_length=1,max_length=10000)
 timeout_seconds:int=Field(ge=1,le=3600)
class HostedBackupRequest(BaseModel):
 pass
class HostedRestoreRequest(BaseModel):
 digest:str
 ciphertext:str
 nonce:str
