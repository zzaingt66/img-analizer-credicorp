import time
import requests
from bs4 import BeautifulSoup
from PIL import Image
import io
from urllib.parse import urljoin, urlparse
import re
import pandas as pd
import os
import argparse

# --- Selenium Imports ---
from selenium import webdriver
from selenium.common import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Constantes de Validación ---
TARGET_RESPONSIVE_WIDTH = 428
TARGET_RESPONSIVE_HEIGHT = 612
DIMENSION_TOLERANCE = 3
WORD_LIMIT_BANNER_2 = 12
EXPECTED_EXTENSION = ".png"
MAX_IMAGE_SIZE_KB = 400
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_KB * 1024
OUTPUT_EXCEL_FILE = "reporte_problemas_carousel.xlsx"

def get_image_details(image_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(image_url, stream=True, timeout=30, headers=headers)
        response.raise_for_status()
        size_in_bytes = int(response.headers.get('content-length', 0))
        image_content = response.content
        if size_in_bytes == 0:
            size_in_bytes = len(image_content)
        img = Image.open(io.BytesIO(image_content))
        width, height = img.size
        return width, height, size_in_bytes
    except requests.exceptions.Timeout:
        print(f"Error: Timeout fetching image {image_url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching image {image_url}: {e}")
        return None
    except Image.UnidentifiedImageError:
        print(f"Error: Cannot identify image file {image_url}. Maybe not an image?")
        return None
    except Exception as e:
        print(f"An unexpected error occurred getting details for {image_url}: {e}")
        return None

def analyze_carousel_images_selenium(page_url, target_width, target_height):
    issues_found = []
    processed_urls = set()
    driver = None
    actual_slide_elements_found = 0

    try:
        print("Setting up WebDriver using webdriver-manager...")
        service = ChromeService(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

        print(f"Initializing WebDriver...")
        driver = webdriver.Chrome(service=service, options=options)
        driver.implicitly_wait(5)

        print(f"Setting window size to {target_width}x{target_height}")
        driver.set_window_size(target_width, target_height)
        time.sleep(1)

        print(f"Fetching page with Selenium: {page_url}")
        driver.get(page_url)

        wait_time = 30
        print(f"Waiting up to {wait_time} seconds for the main banner carousel slides to load...")
        try:
            wait = WebDriverWait(driver, wait_time)
            specific_slide_selector = '.cont-banner .swiper-wrapper > div.swiper-slide:not(.swiper-slide-duplicate)'
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, specific_slide_selector)))
            print("Main banner carousel slides seem to be loaded.")
            time.sleep(5)
        except TimeoutException:
            print(f"Error: Timed out waiting for main banner carousel content ({specific_slide_selector}). Skipping analysis for {page_url}")
            return []

        print("Finding the main banner container and its non-duplicate slides...")
        try:
            main_banner_container = driver.find_element(By.CSS_SELECTOR, '.cont-banner')
            swiper_wrapper_element = main_banner_container.find_element(By.CSS_SELECTOR, '.swiper-wrapper')
            slide_elements = swiper_wrapper_element.find_elements(By.CSS_SELECTOR, ':scope > div.swiper-slide:not(.swiper-slide-duplicate)')
            actual_slide_elements_found = len(slide_elements)
            print(f"Found {actual_slide_elements_found} non-duplicate slide elements within the main banner.")
        except NoSuchElementException:
            print("Error: Could not find the '.cont-banner .swiper-wrapper' structure or slides within it.")
            return []

        if not slide_elements:
            print("Error: No non-duplicate 'swiper-slide' divs found within the main banner wrapper.")
            return []

        for i, slide_element in enumerate(slide_elements):
            data_swiper_index = slide_element.get_attribute('data-swiper-slide-index')
            slide_number_for_report = f"{i + 1} (Index: {data_swiper_index})" if data_swiper_index else f"{i + 1}"

            image_url_to_process = None
            absolute_image_url = None
            actual_width = None
            actual_height = None
            size_bytes = None
            word_count = 0
            notes = []

            print(f"\n--- Analyzing Slide {slide_number_for_report} ---")

            # 1. Obtener URL de la imagen responsive y validar extensión
            try:
                img_element = slide_element.find_element(By.TAG_NAME, 'img')
                image_current_src = img_element.get_attribute('currentSrc')
                image_src = img_element.get_attribute('src')

                if image_current_src and image_current_src.strip():
                    image_url_to_process = image_current_src.strip()
                elif image_src and image_src.strip():
                    image_url_to_process = image_src.strip()


                if image_url_to_process:
                    absolute_image_url = urljoin(page_url, image_url_to_process)
                    print(f"Slide {slide_number_for_report}: Considering Image URL: {absolute_image_url}")

                    if absolute_image_url in processed_urls:
                        print("Skipping already processed URL.")
                        continue

                    # Validar extension
                    try:
                        parsed_url = urlparse(absolute_image_url)
                        _ , extension = os.path.splitext(parsed_url.path)
                        extension = extension.lower()
                        print(f"Detected extension: {extension}")
                        if extension != EXPECTED_EXTENSION:
                            note_ext = f"Extensión incorrecta ({extension}), se esperaba {EXPECTED_EXTENSION}."
                            print(f"VALIDATION FAILED: {note_ext}")
                            notes.append(note_ext)
                        else:
                            print("Extension OK.")
                    except Exception as ext_e:
                        print(f"Error checking extension for {absolute_image_url}: {ext_e}")
                        notes.append("Error al verificar la extensión de la imagen.")


                    # Obtener dimensiones Y KB
                    details = get_image_details(absolute_image_url)
                    if details:
                        actual_width, actual_height, size_bytes = details
                        size_kb = round(size_bytes / 1024, 2)
                        print(f"Actual Dimensions: {actual_width}x{actual_height}, Size: {size_kb} KB")


                        width_ok = abs(actual_width - TARGET_RESPONSIVE_WIDTH) <= DIMENSION_TOLERANCE
                        height_ok = abs(actual_height - TARGET_RESPONSIVE_HEIGHT) <= DIMENSION_TOLERANCE
                        if not (width_ok and height_ok):
                            note_dim = (f"Dimensiones ({actual_width}x{actual_height}) "
                                        f"fuera de rango ({TARGET_RESPONSIVE_WIDTH}x{TARGET_RESPONSIVE_HEIGHT} "
                                        f"+/-{DIMENSION_TOLERANCE}px).")
                            print(f"VALIDATION FAILED: {note_dim}")
                            notes.append(note_dim)
                        else:
                            print("Dimensions OK.")


                        # --- Validacion KB ---
                        if size_bytes > MAX_IMAGE_SIZE_BYTES:
                            note_size = f"Peso ({size_kb} KB) excede el límite de {MAX_IMAGE_SIZE_KB} KB."
                            print(f"VALIDATION FAILED: {note_size}")
                            notes.append(note_size)
                        else:
                            print("Image size OK.")

                    else:
                        print(f"Failed to get details for image: {absolute_image_url}")
                        notes.append("No se pudieron obtener los detalles/dimensiones/peso de la imagen.")

                    processed_urls.add(absolute_image_url)

                else:
                    print(f"Slide {slide_number_for_report}: Could not find image source (currentSrc or src).")
                    notes.append("No se pudo encontrar la URL de la imagen.")


            except NoSuchElementException:
                print(f"Slide {slide_number_for_report}: No <img> tag found.")
                notes.append("No se encontró etiqueta <img> en el slide.")


            # 2. Validar longitud del texto en cont-titles usando JavaScript innerText
            try:
                text_container_elements = slide_element.find_elements(By.CSS_SELECTOR, '.cont-titles')
                if text_container_elements:
                    text_container = text_container_elements[0]
                    text_content = driver.execute_script("return arguments[0].innerText;", text_container).strip()
                    if text_content:
                        words = re.findall(r'\b\w+\b', text_content)
                        word_count = len(words)
                        print(f"Text found in '.cont-titles' (via JS): '{text_content[:60]}...' ({word_count} words)")
                        if word_count > WORD_LIMIT_BANNER_2:
                            note_text = (f"Texto secundario excede {WORD_LIMIT_BANNER_2} palabras "
                                        f"({word_count} encontradas).")
                            print(f"VALIDATION FAILED: {note_text}")
                            notes.append(note_text)
                        else:
                            print("Text length OK.")
                    else:
                        print("'.cont-titles' found but innerText is empty.")
                else:
                    print("No '.cont-titles' div found in this slide.")
            except Exception as e:
                print(f"Error finding/processing text in '.cont-titles': {e}")
                notes.append("Error al procesar el texto secundario.")


            # 3. Registrar el problema si hubo alguna nota
            if notes:
                print(f"Slide {slide_number_for_report}: Issues found. Recording...")
                real_size_kb_report = f"{round(size_bytes / 1024, 2)} KB" if size_bytes is not None else "N/A"

                issues_found.append({
                    'Portal': page_url,
                    'Numero Slide': slide_number_for_report,
                    'URL Imagen Responsive': absolute_image_url if absolute_image_url else "N/A",
                    'Dimensiones Reales': f"{actual_width}x{actual_height}" if actual_width is not None else "N/A",
                    'Peso Real': real_size_kb_report, # <-- Añadir peso al reporte
                    'Palabras Texto Secundario': word_count if word_count > 0 else 0,
                    'Nota': "; ".join(notes)
                })
            else:
                print(f"Slide {slide_number_for_report}: OK.")

    except Exception as e:
        print(f"An critical error occurred during Selenium execution for {page_url}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            print("Closing WebDriver...")
            driver.quit()

    print(f"\nFinished analysis for {page_url}. Found {actual_slide_elements_found} non-duplicate slides. Recorded {len(issues_found)} issues.")
    return issues_found

# --- Función Principal que procesa la lista de URLs ---
def main(urls_to_analyze):
    responsive_width = 428
    responsive_height = 612
    all_issues = []

    for url in urls_to_analyze:
        if not url.startswith(('http://', 'https://')):
            print(f"Skipping invalid URL format: {url}")
            continue
        print(f"\n{'='*20} Analyzing URL: {url} {'='*20}")
        issues = analyze_carousel_images_selenium(url, responsive_width, responsive_height)
        if issues:
            all_issues.extend(issues)

    print("\n--- Analysis Complete for all URLs ---")

    if all_issues:
        print(f"Found {len(all_issues)} total issues across all analyzed URLs. Creating Excel report...")
        df = pd.DataFrame(all_issues)
        cols_order = ['Portal', 'Numero Slide', 'URL Imagen Responsive', 'Dimensiones Reales', 'Peso Real', 'Palabras Texto Secundario', 'Nota']
        cols_order_existing = [col for col in cols_order if col in df.columns]
        df = df[cols_order_existing]

        try:
            df.to_excel(OUTPUT_EXCEL_FILE, index=False, engine='openpyxl')
            print(f"Report saved successfully to '{OUTPUT_EXCEL_FILE}'")
        except Exception as e:
            print(f"Error saving Excel file: {e}")
            print("Attempting to save as CSV instead...")
            try:
                csv_file = OUTPUT_EXCEL_FILE.replace('.xlsx', '.csv')
                df.to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"Report saved as CSV: '{csv_file}'")
            except Exception as ce:
                print(f"Could not save as CSV either: {ce}")

    else:
        print(f"No issues found meeting the criteria (dimension mismatch, size > {MAX_IMAGE_SIZE_KB}KB, text length, or wrong extension) in the analyzed carousels.")


# --- Punto de Entrada Principal y Manejo de Argumentos ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carrusel analizer para credicorp solo funciona con credicorp xddd y genera un reporte.")
    parser.add_argument(
        '--urls',
        metavar='URL',
        type=str,
        nargs='+',
        required=True,
        help='Una o más URLs completas de las páginas a analizar (separadas por espacios).'
    )
    args = parser.parse_args()
    main(args.urls)
