# Epoch — Merge Guide

## Result

The `combined-python-app` branch contains Epoch, combining the original productivity application with the Study Hub/document/RAG/AI features from `Divij808/test`.

The application remains one Flask application and one Python backend.

## 1. Choose Epoch as the base

The original application application was used as the technical base because it already contained the main authentication, SQLite database, tasks, habits, goals, health, rewards, loans, quick links and custom pages.

The separate `test` Flask app was not copied as a second application. Its useful modules were moved into the existing Flask application instead.

## 2. Move the document-processing modules

These modules were copied from `test`:

- `document/reader.py` — extracts text from TXT and PDF files.
- `document/chunker.py` — splits extracted text into overlapping chunks.
- `rag/retriever.py` — stores chunks and performs simple keyword-based retrieval.
- `ai/generator.py` — loads the local Hugging Face model and generates answers, summaries, notes, flashcards and practice questions.

The packages now live directly inside the Epoch project.

## 3. Add the Study Hub routes

The second Flask application's routes were rewritten into the existing `app.py`.

The new routes are:

- `/study` — Study Hub home.
- `/study/sources` — source management.
- `/study/upload` — PDF/TXT upload and indexing.
- `/study/delete/<source_id>` — delete a source.
- `/study/workspace` — questions and AI generation.

Each route uses the existing Epoch login system, so a user must be logged in before accessing Study Hub.

## 4. Separate the HTML pages

The original application monolithic `dashboard.html` was removed.

The application now uses separate templates for the major pages:

- `base.html`
- `login.html`
- `signup.html`
- `main_menu.html`
- `tasks.html`
- `habits.html`
- `shopping.html`
- `quick_links.html`
- `loans.html`
- `goals.html`
- `health.html`
- `custom_page.html`
- `study_home.html`
- `study_sources.html`
- `study_workspace.html`

`base.html` contains the shared navigation and page layout. The individual pages extend it with Jinja.

## 5. Remove JavaScript from the merged application

The JavaScript file from `test` was deliberately not copied.

Study Hub now uses ordinary Flask forms:

1. Browser submits a POST request.
2. Flask validates the request.
3. Python performs the operation.
4. Flask redirects or renders the result.
5. Jinja displays the result.

For example, uploading a source uses a normal multipart POST to `/study/upload`, rather than JavaScript/fetch.

The old monolithic dashboard was also removed from the rendering path.

## 6. Keep Study Hub data separate per user

Uploaded files and indexes are stored below:

`epoch_uploads/<username>/`
`epoch_data/<username>/`

This prevents one logged-in user from accidentally searching another user's Study Hub index.

## 7. Install dependencies

From the `Epoch` directory:

```bash
pip install -r requirements.txt
```

The additional Study Hub dependencies include:

- `pypdf`
- `transformers`
- `torch`

The existing Epoch Google Calendar dependencies remain listed as well.

## 8. Run the application

From the `Epoch` directory:

```bash
python app.py
```

Then open the Flask address shown in the terminal.

Create an account or log in, then select **Study Hub** from the navigation.

## 9. Test the merged application

Test in this order:

### Authentication
- Create a new account.
- Log in with the account.
- Log out.
- Confirm Study Hub redirects unauthenticated users to login.

### Original Epoch features
- Create a task.
- Complete a task and check coins.
- Create a habit.
- Create a goal.
- Open Rewards.
- Open Health.
- Open Quick Links.

### Study Hub
- Open Sources.
- Upload a TXT file.
- Upload a PDF file.
- Confirm the source appears.
- Open Workspace.
- Ask a question about the uploaded material.
- Generate a summary.
- Generate revision notes.
- Generate flashcards.
- Generate practice questions.
- Delete the source and confirm it disappears.

## 10. Important first-run note

The local AI model is loaded lazily: it is not loaded when the Flask application starts. It is loaded the first time the Study Hub Workspace needs AI generation.

The default model is:

`google/flan-t5-base`

You can change it with the `EPOCH_MODEL` environment variable.

## 11. Git structure

The final structure is conceptually:

```text
Epoch/
├── app.py
├── requirements.txt
├── ai/
│   ├── __init__.py
│   └── generator.py
├── document/
│   ├── __init__.py
│   ├── reader.py
│   └── chunker.py
├── rag/
│   ├── __init__.py
│   └── retriever.py
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── signup.html
│   ├── main_menu.html
│   ├── tasks.html
│   ├── habits.html
│   ├── shopping.html
│   ├── quick_links.html
│   ├── loans.html
│   ├── goals.html
│   ├── health.html
│   ├── custom_page.html
│   ├── study_home.html
│   ├── study_sources.html
│   └── study_workspace.html
└── static/
    └── style.css
```

The old `dashboard.html`, the `test` application's `script.js`, and the committed Google Calendar `token.json` are not part of the merged application.
