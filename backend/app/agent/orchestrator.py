import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.config import settings

class AgentOrchestrator:
    def __init__(self):
        kwargs = {
            "model": settings.openai_model,
            "api_key": settings.openai_api_key,
            "temperature": 0
        }
        if settings.openai_api_base:
            kwargs["base_url"] = settings.openai_api_base
            kwargs["default_headers"] = {"api-key": settings.openai_api_key}

        self.llm = ChatOpenAI(**kwargs) if settings.openai_api_key and settings.openai_api_key != "dummy-key" else None
        
        self.system_prompt = """You are an expert Linux sysadmin and AI Service Desk Autopilot.
Your job is to diagnose and fix incidents on Ubuntu servers based on a ticket description.
You MUST output ONLY a valid JSON object representing your next step. Do not add markdown formatting or extra text outside the JSON.
You can either PROPOSE a command to run, or SUBMIT the final activity report if the issue is fixed.
Do not use interactive commands like `nano`, `vi`, or `less`. Use `cat`, `grep`, `head`, `tail` instead.

Output schema for proposing command:
{
  "type": "command",
  "reasoning": "Explain why you are running this command...",
  "command": "systemctl status nginx"
}

Output schema for final report (use this ONLY when you are absolutely sure the fix is validated):
{
  "type": "report",
  "reasoning": "The issue is fixed because...",
  "summary": "One-sentence summary of what was restored.",
  "root_cause": "The technical root cause — not the symptom.",
  "actions_taken": "Diagnosis and fix steps, in order.",
  "commands_summary": "Relevant commands / command classes — no secrets.",
  "validation_result": "Concrete proof the customer benefit is restored."
}"""

    async def get_next_step(self, ticket: dict, system: dict, history: list):
        if not self.llm:
            # Dummy mode for no-key testing
            if len(history) > 3:
                return {
                    "type": "report",
                    "reasoning": "Dummy run finished.",
                    "summary": "Fixed the dummy issue.",
                    "root_cause": "Dummy root cause.",
                    "actions_taken": "Ran some dummy commands.",
                    "commands_summary": "ls, cat",
                    "validation_result": "Looks good."
                }
            return {
                "type": "command", 
                "reasoning": "Checking OS release as a test.", 
                "command": "cat /etc/os-release"
            }

        history_str = json.dumps([h.model_dump() for h in history], indent=2)
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=f"Ticket: {json.dumps(ticket)}\nSystem: {json.dumps(system)}\nHistory:\n{history_str}")
        ]
        
        response = await self.llm.ainvoke(messages)
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            return json.loads(content)
        except Exception as e:
            return {
                "type": "command",
                "reasoning": f"Failed to parse LLM output. Fallback. Error: {str(e)} | Output: {response.content}",
                "command": "echo 'LLM Parse Error'"
            }

orchestrator = AgentOrchestrator()
