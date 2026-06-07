import httpx
from app.core.config import settings

class ERPClient:
    def __init__(self):
        self.base_url = settings.phoenix_api_base_url.rstrip("/")
        self.token = settings.phoenix_api_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def get_my_tickets(self, status: str = None, priority: str = None, sort: str = None):
        params = {}
        if status: params["status"] = status
        if priority: params["priority"] = priority
        if sort: params["sort"] = sort
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/v1/me/tickets", headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_ticket(self, ticket_id: int):
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/v1/tickets/{ticket_id}", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_customer_system(self, ticket_id: int):
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/v1/tickets/{ticket_id}/customer-system", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def create_activity(self, activity: dict):
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(f"{self.base_url}/api/v1/activities/create", headers=self.headers, json=activity)
            resp.raise_for_status()
            return resp.json()

erp_client = ERPClient()
