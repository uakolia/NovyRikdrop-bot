FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python install_solution.py && python -m unittest test_price_solution.py && python -m compileall -q .
CMD ["python", "main.py"]
