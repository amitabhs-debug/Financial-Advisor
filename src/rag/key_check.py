import anthropic
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=50,
    messages=[{"role": "user", "content": "Say hello in one sentence."}]
)
print(resp.content[0].text)