import os
import json
from typing import List, Optional, Union
from openai import AzureOpenAI
import instructor
from source.medical_chatbot.context import INIT_SYSTEM_CONTEXT
from pydantic import BaseModel, Field, ConfigDict
import logging
from dotenv import load_dotenv
from pymongo import MongoClient
import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)  # Add basic logging config

load_dotenv()

DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
MONGODB_CONNECTION_STRING = os.getenv("MONGODB_CONNECTION_STRING")
MONGODB_DATABASE_NAME = os.getenv("MONGODB_DATABASE_NAME", "medical_chatbot")
MONGODB_COLLECTION_NAME = os.getenv("MONGODB_COLLECTION_NAME", "chat_sessions")

# Initialize MongoDB client
try:
    mongo_client = MongoClient(MONGODB_CONNECTION_STRING)
    db = mongo_client[MONGODB_DATABASE_NAME]
    chat_sessions_collection = db[MONGODB_COLLECTION_NAME]
    chat_sessions_collection.create_index("session_id")  # faster queries
    logger.info("MongoDB connection established successfully")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    chat_sessions_collection = None
    os.makedirs("Storage/chat_memory", exist_ok=True)

# Azure OpenAI client
client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
)
client = instructor.patch(client, mode=instructor.Mode.TOOLS)


# --------- Pydantic Models ---------
class Product_analysis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    match_score: int = Field(
        ..., description="Confidence score (1-100) for relevance.", ge=0, le=100
    )
    ingredients_list: List[str] = Field(..., description="Key ingredients list.")
    benefits_of_your_skin: str = Field(
        ..., description="Potential benefits based on ingredients and user context."
    )
    potential_concerns: str = Field(
        ..., description="Potential side effects or concerns."
    )
    usage_recommendations: str = Field(
        ..., description="General recommendations for use."
    )


class ClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: Optional[str] = Field(
        None, description="Next question to ask OR a direct response."
    )
    summary: Optional[str] = Field(
        None,
        description="Provide summary if chat ends or enough info gathered.",
    )
    product_analysis: Optional[Product_analysis] = Field(
        None, description="Analysis of a skin product if requested."
    )


# --------- LLM Call ---------
def llm_reply(chat_history: list) -> Union[ClassificationResponse, str]:
    """Send full chat history to LLM and return structured response."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_model=ClassificationResponse,
            messages=chat_history,
            max_retries=1,
            temperature=0.1,
        )
        return response
    except Exception as e:
        logger.error(f"LLM call failed: {e}", exc_info=True)
        return f"Error communicating with LLM: {type(e).__name__} - {e}"


# --------- Chatbot Class ---------
class MedicalChatBot:
    def __init__(
        self,
        session_id: str,
        query: Optional[str] = None,
        image_base64: Optional[str] = None,
    ):
        if not session_id:
            raise ValueError("session_id cannot be empty")
        self.session_id = session_id
        self.query = query.strip() if query else None
        self.image_base64 = image_base64
        self.chat_history = self._load_chat_history()

    # ----- Load Chat History -----
    def _load_chat_history(self) -> List[dict]:
        if chat_sessions_collection is not None:
            try:
                session_data = chat_sessions_collection.find_one(
                    {"session_id": self.session_id}
                )
                if session_data and "chat_history" in session_data:
                    if (
                        session_data["chat_history"]
                        and session_data["chat_history"][0].get("role") == "system"
                    ):
                        return session_data["chat_history"]
            except Exception as e:
                logger.error(f"Error loading from MongoDB: {e}")

        try:
            local_file = f"Storage/chat_memory/{self.session_id}.json"
            if os.path.exists(local_file):
                with open(local_file, "r") as file:
                    history = json.load(file)
                    if isinstance(history, list) and history and history[0].get("role") == "system":
                        return history
        except Exception as e:
            logger.error(f"Error loading from local storage: {e}")

        # Default system prompt
        return [{"role": "system", "content": INIT_SYSTEM_CONTEXT}]

    # ----- Save Chat History -----
    def _save_chat_history(self) -> None:
        if chat_sessions_collection is not None:
            try:
                chat_sessions_collection.update_one(
                    {"session_id": self.session_id},
                    {
                        "$set": {
                            "chat_history": self.chat_history,
                            "last_updated": datetime.datetime.utcnow(),
                        }
                    },
                    upsert=True,
                )
                return
            except Exception as e:
                logger.error(f"Error saving to MongoDB: {e}")

        try:
            local_file = f"Storage/chat_memory/{self.session_id}.json"
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            with open(local_file, "w") as file:
                json.dump(self.chat_history, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving to local storage: {e}")

    # ----- Chat Flow -----
    def chatbot_flow(self) -> dict:
        if not self.query and not self.image_base64:
            logger.warning("Chatbot flow called with no query or image.")
            return {"error": "Please provide a query or an image."}

        # Build user message
        user_content_parts = []
        if self.query:
            user_content_parts.append({"type": "text", "text": self.query})
        if self.image_base64:
            if not self.image_base64.startswith("data:image"):
                logger.error("Invalid image_base64 format received.")
                self.chat_history.append(
                    {
                        "role": "user",
                        "content": self.query if self.query else "[User provided an invalid image]",
                    }
                )
                self.chat_history.append(
                    {
                        "role": "assistant",
                        "content": "There seems to be an issue with the image you provided. Could you try uploading it again or describe your concern?",
                    }
                )
                self._save_chat_history()
                return {
                    "question": "There seems to be an issue with the image you provided. Could you try uploading it again or describe your concern?"
                }

            user_content_parts.append(
                {"type": "image_url", "image_url": {"url": self.image_base64, "detail": "auto"}}
            )

        if len(user_content_parts) == 1 and user_content_parts[0]["type"] == "text":
            final_user_content = user_content_parts[0]["text"]
        elif len(user_content_parts) > 0:
            final_user_content = user_content_parts
        else:
            logger.error("Cannot proceed with empty user content.")
            return {"error": "Internal error: No user content generated."}

        # Add user query
        self.chat_history.append({"role": "user", "content": final_user_content})

        # Call LLM
        llm_response = llm_reply(self.chat_history)

        # Handle LLM Response
        if isinstance(llm_response, ClassificationResponse):
            logger.info("LLM call successful.")

            # --- FIX: Ensure match_score is never 0 ---
            if llm_response.product_analysis and llm_response.product_analysis.match_score == 0:
                logger.warning("LLM returned match_score=0, correcting to 1.")
                llm_response.product_analysis.match_score = 1

            # Human-readable assistant reply
            assistant_text = (
                llm_response.summary
                or llm_response.question
                or (llm_response.product_analysis.benefits_of_your_skin if llm_response.product_analysis else "")
                or "Here are my observations."
            )

            # Add readable text to history
            self.chat_history.append({"role": "assistant", "content": assistant_text})

            # Save structured JSON separately
            structured_response = llm_response.model_dump()
            if chat_sessions_collection is not None:
                chat_sessions_collection.update_one(
                    {"session_id": self.session_id},
                    {"$push": {"structured_responses": structured_response}},
                    upsert=True,
                )

            self._save_chat_history()
            return structured_response

        elif isinstance(llm_response, str):
            logger.error(f"LLM call failed, returning error: {llm_response}")
            return {"error": f"Chatbot processing error: {llm_response}"}
        else:
            logger.error(f"Unexpected response type: {type(llm_response)}")
            return {"error": "Internal chatbot error: Unexpected response format."}


# Example run
if __name__ == "__main__":
    session_id = "test_session_123"
    query = "I have this red spot on my arm, what could it be?"

    if chat_sessions_collection:
        chat_sessions_collection.delete_one({"session_id": session_id})
    else:
        local_file = f"Storage/chat_memory/{session_id}.json"
        if os.path.exists(local_file):
            os.remove(local_file)

    chatbot = MedicalChatBot(session_id=session_id, query=query)
    response = chatbot.chatbot_flow()
    print("Response:", response)
