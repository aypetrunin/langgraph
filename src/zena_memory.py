# import os
# from dotenv import load_dotenv

# from mem0 import AsyncMemoryClient, MemoryClient



# load_dotenv()

# mem0_api_key = os.getenv("MEM0AI")
# memory = AsyncMemoryClient(api_key=mem0_api_key)

# Configure memory filtering and categories
# memory.project.update(
#     custom_instructions="""
#     Extract: query, indications, contraindications, preferences, body part, selected item, services found
#     Exclude: greetings, filler, casual chat
#     """,
#     custom_categories=[
#         {"name": "indications", "description": "The client's goals, wishes, and indications when searching for services"},
#         {"name": "contraindications", "description": "Client contraindications "},
#         {"name": "preferences", "description": "Client preferences when choosing services"},
#         {"name": "body part", "description": "Части тела на которую нужно провести услугу"},
#         {"name": "query", "description": "A brief definition of what the customer wants"},
#         {"name": "selected item", "description": "The service selected by the client"},
#         {"name": "services found", "description": "services provided by the assistant to the client"},
#     ]
# )

# Configure memory filtering and categories
# memory.project.update(
#     custom_instructions=""" 
#     """,
#     custom_categories=[
#     ]
# )