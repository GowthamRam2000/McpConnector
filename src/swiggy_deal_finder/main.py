from fastapi import FastAPI

app = FastAPI(title="Swiggy Deal Finder")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
