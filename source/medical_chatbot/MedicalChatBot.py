import os
import json
from typing import List, Tuple, Optional, Union
from openai import AzureOpenAI
import instructor
from source.medical_chatbot.context import INIT_SYSTEM_CONTEXT
from pydantic import BaseModel, Field, ConfigDict
import traceback
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)  # Add basic logging config

# Ensure Storage directory exists
os.makedirs("Storage/chat_memory", exist_ok=True)

# Use environment variables for sensitive data - replace placeholders before running

# Remove these lines since we're setting them in __init__.py
#AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
#API_KEY = os.getenv("AZURE_API_KEY")
#API_VERSION = os.getenv("AZURE_API_VERSION")
#DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")

DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")

client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
)

client = instructor.patch(client, mode=instructor.Mode.TOOLS)

class Product_analysis(BaseModel):
    model_config = ConfigDict(
        extra="ignore"
    )  # Ignore extra fields if API returns more than defined
    match_score: int = Field(
        ...,
        description="Confidence score (0-100) indicating how relevant the product is to the user's likely skin type or concerns based on the conversation.",
        gt=0,
        lt=101,
    )  # Adjusted range to be inclusive of 100
    ingredients_list: List[str] = Field(
        ..., description="List of key ingredients identified in the product."
    )
    benefits_of_your_skin: str = Field(
        ...,
        description="Explanation of potential benefits based on ingredients and user's context.",
    )
    potential_concerns: str = Field(
        ...,
        description="Explanation of potential concerns or side effects based on ingredients and user's context (e.g., irritation, allergies).",
    )  # Fixed typo: poteintial -> potential
    usage_recommendations: str = Field(
        ...,
        description="General recommendations on how to use the product or integrate it into a routine.",
    )


class ClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore extra fields
    question: Optional[str] = Field(
        None,
        description="The next question to ask the user to gather more information for diagnosis, OR a direct response if no further questions needed but no summary/analysis.",
    )
    summary: Optional[str] = Field(
        None,
        description="Provide a summary ONLY if the user indicates they want to end the chat (e.g., 'bye', 'thanks that's all') OR if the chatbot has gathered enough information to create a comprehensive overview for a dermatologist. The summary should be clean and concise.",
    )
    product_analysis: Optional[Product_analysis] = Field(
        None, description="Analysis of a skin product if requested by the user."
    )  # Renamed for consistency: Product_analysis -> product_analysis


# --- Function to handle LLM communication ---
def llm_reply(
    chat_history: list,
) -> Union[ClassificationResponse, str]:  # Return type can be model or error string
    """
    Sends the chat history to the LLM and expects a structured response.

    Args:
        chat_history: The complete list of messages in the conversation.

    Returns:
        A ClassificationResponse object on success, or an error string on failure.
    """
    try:
        # Debug print for the messages being sent
        # print("----->>> Sending messages to LLM:", json.dumps(chat_history, indent=2))

        response = client.chat.completions.create(
            model="gpt-4o",  # Use deployment name
            response_model=ClassificationResponse,
            messages=chat_history,  # *** FIX: Pass the entire history ***
            max_retries=1,  # Reduce retries for faster failure feedback during debugging
            temperature=0.1
        )
        # print("----->>> Raw LLM Response:", response) # Optional: print raw response
        return response
    except Exception as e:
        logger.error(f"LLM call failed: {e}", exc_info=True)
        # Construct a more informative error message
        error_message = f"Error communicating with LLM: {type(e).__name__} - {e}"
        print(
            f"Error in llm_reply: {error_message}"
        )  # Print error for immediate feedback
        # Also print traceback for detailed debugging
        # traceback.print_exc()
        return error_message  # Return the error string

# Note: Ensure that the Azure OpenAI client is properly configured with your credentials.
# --- Main Chatbot Class ---
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
        self.query = query.strip() if query else None  # Handle None query
        self.image_base64 = image_base64
        self.chat_history_file = f"Storage/chat_memory/{self.session_id}.json"
        self.chat_history = self._load_chat_history()

    def _load_chat_history(self) -> List[dict]:  # Return type hint uses dict
        try:
            # Ensure the directory exists before trying to read
            os.makedirs(os.path.dirname(self.chat_history_file), exist_ok=True)
            with open(self.chat_history_file, "r") as file:
                history = json.load(file)
                # Basic validation: check if it's a list and has the system message
                if (
                    isinstance(history, list)
                    and history
                    and history[0].get("role") == "system"
                ):
                    return history
                else:
                    logger.warning(
                        f"Chat history file {self.chat_history_file} has invalid format. Reinitializing."
                    )
                    return [{"role": "system", "content": INIT_SYSTEM_CONTEXT}]
        except FileNotFoundError:
            logger.info(
                f"Chat history file {self.chat_history_file} not found. Initializing new history."
            )
            return [{"role": "system", "content": INIT_SYSTEM_CONTEXT}]
        except json.JSONDecodeError:
            logger.error(
                f"Error decoding JSON from {self.chat_history_file}. Reinitializing history."
            )
            return [{"role": "system", "content": INIT_SYSTEM_CONTEXT}]
        except Exception as e:
            logger.error(f"Unexpected error loading chat history: {e}", exc_info=True)
            return [{"role": "system", "content": INIT_SYSTEM_CONTEXT}]

    def _save_chat_history(self) -> None:
        try:
            # Ensure the directory exists before trying to write
            os.makedirs(os.path.dirname(self.chat_history_file), exist_ok=True)
            with open(self.chat_history_file, "w") as file:
                json.dump(
                    self.chat_history, file, indent=4
                )  # Add indent for readability
        except Exception as e:
            logger.error(
                f"Failed to save chat history to {self.chat_history_file}: {e}",
                exc_info=True,
            )

    def chatbot_flow(self) -> dict:
        """
        Manages the flow of the chatbot interaction for one turn.
        """
        if not self.query and not self.image_base64:
            logger.warning("Chatbot flow called with no query or image.")
            # Decide how to handle this - maybe return an error or a prompt?
            return {"error": "Please provide a query or an image."}

        # --- Construct the user message content ---
        user_content_parts = []
        if self.query:
            user_content_parts.append({"type": "text", "text": self.query})
        if self.image_base64:
            # Basic check if base64 string looks valid (starts with data:image/)
            if not self.image_base64.startswith("data:image"):
                logger.error("Invalid image_base64 format received.")
                # Append an error message for the user *and* potentially return error
                self.chat_history.append(
                    {
                        "role": "user",
                        "content": (
                            self.query
                            if self.query
                            else "[User provided an invalid image]"
                        ),
                    }
                )
                self.chat_history.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "question": "There seems to be an issue with the image you provided. Could you try uploading it again or describe your concern?"
                            }
                        ),
                    }
                )
                self._save_chat_history()
                return {
                    "question": "There seems to be an issue with the image you provided. Could you try uploading it again or describe your concern?"
                }

            user_content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self.image_base64,
                        "detail": "auto",
                    },  # Use 'auto' or 'low'/'high'
                }
            )

        # If only text, content is string; if image involved, content is list
        if len(user_content_parts) == 1 and user_content_parts[0]["type"] == "text":
            final_user_content = user_content_parts[0]["text"]
        elif len(user_content_parts) > 0:
            final_user_content = user_content_parts
        else:
            # This case should ideally be caught earlier, but handle defensively
            logger.error("Cannot proceed with empty user content.")
            return {"error": "Internal error: No user content generated."}

        # Add user message to history
        self.chat_history.append({"role": "user", "content": final_user_content})

        # --- Call LLM ---
        llm_response = llm_reply(self.chat_history)  # Pass the full history

        # --- Process LLM Response ---
        if isinstance(llm_response, ClassificationResponse):
            logger.info("LLM call successful.")
            # Successfully received a structured response
            assistant_response_json = llm_response.model_dump_json(indent=2)
            self.chat_history.append(
                {"role": "assistant", "content": assistant_response_json}
            )
            self._save_chat_history()
            return llm_response.model_dump()  # Return the response as a dictionary

        elif isinstance(llm_response, str):
            # LLM call failed, llm_reply returned an error string
            logger.error(f"LLM call failed, returning error to client: {llm_response}")
            # You might want to add a generic failure message to the history
            # self.chat_history.append({
            #     "role": "assistant",
            #     "content": json.dumps({"question": "I encountered an issue processing your request. Please try again later."})
            # })
            # self._save_chat_history() # Optionally save the state even on failure
            return {"error": f"Chatbot processing error: {llm_response}"}
        else:
            # Unexpected return type from llm_reply
            logger.error(
                f"Unexpected response type from llm_reply: {type(llm_response)}"
            )
            return {"error": "Internal chatbot error: Unexpected response format."}


# Example Usage (for testing)
if __name__ == "__main__":
    session_id = "test_session_123"
    query = "I have this red spot on my arm, what could it be?"
    # To test with an image, provide a valid base64 string here
    # Example: image_base64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA..."
    image_base64 = None  # Or your base64 string

    # Clear previous history for clean test
    history_file = f"Storage/chat_memory/{session_id}.json"
    if os.path.exists(history_file):
        os.remove(history_file)

    chatbot = MedicalChatBot(
        session_id=session_id, query=query, image_base64=image_base64
    )
    response = chatbot.chatbot_flow()
