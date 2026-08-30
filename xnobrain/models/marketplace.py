from pydantic import BaseModel
class MarketplaceInstallRequest(BaseModel):
 package: dict
class MarketplaceUninstallRequest(BaseModel):
 local_profile_id: str
class MarketplaceUpdateRequest(BaseModel):
 package: dict
 local_profile_id: str
