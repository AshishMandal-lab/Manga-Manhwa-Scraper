# Manga-Manhwa-Scraper Gallery Scraper

A multi-site gallery and manga scraper built with Flask, Selenium, and a dynamic animated UI.

## Features

-   **Multi-Site Support:** Scrapes from Rule34, E-Hentai, N-Hentai, Bato.to, and ImHentai.
-   **Background Processing:** Uses threading to run scrape jobs without freezing the UI.
-   **Live Progress:** Real-time progress bar and status updates.
-   **Dynamic UI:** Animated sphere-based navigation built with anime.js.
-   **Dual Viewers:** Includes a 3D cover-flow viewer and a vertical manhwa viewer.
-   **Gallery Library:** All downloads are saved and viewable in a local library.

## Setup and Installation

1.  Clone the repository:
    `git clone https://github.com/your-username/Manga-Manhwa-Scraper.git`
2.  Navigate into the project directory:
    `cd Manga-Manhwa-Scraper`
3.  Install the required Python packages:
    `pip install -r requirements.txt`
4.  You may also need to have Google Chrome installed for the Selenium driver.

## How to Run

1.  Run the Flask application from the terminal:
    `flask run`
2.  Open your web browser and go to `http://127.0.0.1:5000`
