"""Local browser smoke test; no portal requests, credentials, or Telegram messages."""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<title>Browser works!</title>')
            assert page.title() == 'Browser works!'
            print('Browser works! Chromium launched and rendered a local page.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
