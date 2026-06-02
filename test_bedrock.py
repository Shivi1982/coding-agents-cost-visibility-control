from openai import OpenAI

# Requires:
# OPENAI_API_KEY=<your Bedrock long-term API key>
# OPENAI_BASE_URL=https://bedrock-mantle.us-east-2.api.aws/openai/v1/responses

client = OpenAI()

response = client.responses.create(
    model="openai.gpt-5.5",
    input="one example to use game theory with Large Language Models"
)

print(response)
