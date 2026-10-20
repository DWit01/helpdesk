from fastapi import FastAPI

# init FastAPi as app
app = FastAPI()


# register a route
@app.get("/")
async def root():
    return {"message": "Hello World, this is my first fastAPI program"}
