cd ..
start powershell -NoExit -Command "npm run -w frontend dev"

cd backend
python -m flask --app app.main:app --debug run --host=0.0.0.0 --port=5000