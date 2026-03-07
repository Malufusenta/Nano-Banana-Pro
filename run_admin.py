"""
Запуск админ-панели: python run_admin.py
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "admin_panel.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1
    )