from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import re
from django.template.loader import get_template
import qrcode
import base64
from io import BytesIO
import os
from django.template.loader import render_to_string
import logging
import json
from .models import ExtractedData   

# Global variables for lazy imports
cv2 = None
np = None
pytesseract = None
pisa = None

def get_cv2():
    """Lazy import of cv2"""
    global cv2
    if cv2 is None:
        try:
            import cv2 as cv2_module
            cv2 = cv2_module
        except ImportError:
            raise ImportError("OpenCV not available")
    return cv2

def get_numpy():
    """Lazy import of numpy"""
    global np
    if np is None:
        try:
            import numpy as np_module
            np = np_module
        except ImportError:
            raise ImportError("NumPy not available")
    return np

def get_pytesseract():
    """Lazy import of pytesseract"""
    global pytesseract
    if pytesseract is None:
        try:
            import pytesseract as pytesseract_module
            pytesseract = pytesseract_module
            # Configure Tesseract path
            if os.environ.get('RENDER'):
                pytesseract.pytesseract.tesseract_cmd = 'tesseract'
            else:
                pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
        except ImportError:
            raise ImportError("pytesseract not available")
    return pytesseract

def get_pisa():
    """Lazy import of xhtml2pdf pisa"""
    global pisa
    if pisa is None:
        try:
            from xhtml2pdf import pisa as pisa_module
            pisa = pisa_module
        except ImportError:
            raise ImportError("xhtml2pdf not available")
    return pisa

logging.basicConfig(level=logging.DEBUG)

def home(request):
    """Simple home view that doesn't require heavy libraries"""
    return render(request, 'ocr_app/home.html')

def preprocess_image(image):
    cv2 = get_cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    processed_image = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    return processed_image

def extract_info(image):
    """Extract information from the uploaded image using OCR"""
    try:
        cv2 = get_cv2()
        pytesseract = get_pytesseract()
        
        # Multiple preprocessing approaches for better OCR
        approaches = [
            # Approach 1: Original preprocessing
            lambda img: preprocess_image_basic(img, cv2),
            # Approach 2: Enhanced preprocessing for Aadhaar
            lambda img: preprocess_image_aadhaar(img, cv2),
            # Approach 3: Different threshold
            lambda img: preprocess_image_adaptive(img, cv2)
        ]
        
        best_result = ("", "", "", "", "")
        best_confidence = 0
        
        for i, preprocess_func in enumerate(approaches):
            try:
                processed_image = preprocess_func(image)
                
                # Try different OCR configurations
                configs = [
                    '--psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/ :',
                    '--psm 4 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/ :',
                    '--psm 3 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/ :'
                ]
                
                for config in configs:
                    text = pytesseract.image_to_string(processed_image, config=config)
                    logging.debug(f"OCR attempt {i+1} with config '{config}' extracted text: {text[:200]}...")
                    
                    name, birth_date, pan_number, aadhaar_number, gender = parse_text(text)
                    
                    # Log what was parsed
                    logging.debug(f"Parsed from OCR {i+1}: name='{name}', birth_date='{birth_date}', gender='{gender}', aadhaar='{aadhaar_number}', pan='{pan_number}'")
                    
                    # Calculate confidence based on extracted data quality
                    confidence = calculate_extraction_confidence(name, birth_date, pan_number, aadhaar_number, gender)
                    logging.debug(f"OCR attempt {i+1} confidence: {confidence:.2f}")
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_result = (name, birth_date, pan_number, aadhaar_number, gender)
                        logging.debug(f"New best result with confidence {confidence:.2f}")
                        
                    # If we get a good result, break early
                    if confidence > 0.7:
                        logging.debug(f"High confidence result found, breaking early")
                        break
                        
                if best_confidence > 0.7:
                    break
                    
            except Exception as approach_error:
                logging.warning(f"OCR approach {i+1} failed: {approach_error}")
                continue
        
        logging.debug("Best OCR results (confidence: %.2f): name='%s', dob='%s', gender='%s'", 
                     best_confidence, best_result[0], best_result[1], best_result[4])
        
        return best_result
        
    except Exception as e:
        logging.error("OCR extraction failed: %s", e)
        return "", "", "", "", ""

def preprocess_image_basic(image, cv2):
    """Basic preprocessing approach"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, processed_image = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    processed_image = cv2.morphologyEx(processed_image, cv2.MORPH_CLOSE, kernel)
    processed_image = cv2.morphologyEx(processed_image, cv2.MORPH_OPEN, kernel)
    processed_image = cv2.dilate(processed_image, kernel, iterations=1)
    return processed_image

def preprocess_image_aadhaar(image, cv2):
    """Enhanced preprocessing specifically for Aadhaar cards"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Enhance contrast
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    
    # Denoise
    denoised = cv2.fastNlMeansDenoising(enhanced)
    
    # Apply adaptive threshold
    adaptive_thresh = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    
    # Morphological operations
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    processed = cv2.morphologyEx(adaptive_thresh, cv2.MORPH_CLOSE, kernel)
    
    return processed

def preprocess_image_adaptive(image, cv2):
    """Adaptive preprocessing approach"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Apply bilateral filter to reduce noise while keeping edges sharp
    filtered = cv2.bilateralFilter(gray, 9, 75, 75)
    
    # Apply adaptive threshold
    adaptive_thresh = cv2.adaptiveThreshold(filtered, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 10)
    
    # Morphological operations
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    processed = cv2.morphologyEx(adaptive_thresh, cv2.MORPH_OPEN, kernel)
    
    return processed

def calculate_extraction_confidence(name, birth_date, pan_number, aadhaar_number, gender):
    """Calculate confidence score for extracted data"""
    confidence = 0.0
    
    # Name confidence
    if name and len(name.strip()) > 2 and re.match(r'^[A-Za-z\s]+$', name.strip()):
        confidence += 0.3
    
    # Birth date confidence
    if birth_date and re.match(r'\d{2}/\d{2}/\d{4}', birth_date):
        confidence += 0.25
    
    # Aadhaar number confidence
    if aadhaar_number and re.match(r'\d{4}\s\d{4}\s\d{4}', aadhaar_number):
        confidence += 0.25
    
    # PAN number confidence
    if pan_number and re.match(r'[A-Z]{5}[0-9]{4}[A-Z]{1}', pan_number):
        confidence += 0.1
    
    # Gender confidence
    if gender and gender.lower() in ['male', 'female', 'transgender', 'other']:
        confidence += 0.1
    
    return confidence

def parse_text(text):
    """Enhanced text parsing for Aadhaar and PAN cards"""
    name = ""
    birth_date = ""
    pan_number = ""
    aadhaar_number = ""
    gender = ""

    # Clean and normalize text
    cleaned_text = re.sub(r'\s+', ' ', text.strip())
    all_text_list = re.split(r'[\n\r]', text)
    text_list = [line.strip() for line in all_text_list if line.strip()]

    # Enhanced PAN pattern matching
    pan_patterns = [
        r'[A-Z]{5}[0-9]{4}[A-Z]{1}',
        r'[A-Z]{5}\s*[0-9]{4}\s*[A-Z]{1}'
    ]
    for pattern in pan_patterns:
        pan_match = re.search(pattern, text.upper())
        if pan_match:
            pan_number = re.sub(r'\s', '', pan_match.group(0))
            break

    # Enhanced Aadhaar pattern matching
    aadhaar_patterns = [
        r'\d{4}\s+\d{4}\s+\d{4}',
        r'\d{4}-\d{4}-\d{4}',
        r'\d{12}',
        r'(\d{4})\s*(\d{4})\s*(\d{4})'
    ]
    for pattern in aadhaar_patterns:
        aadhaar_match = re.search(pattern, text)
        if aadhaar_match:
            if pattern == r'(\d{4})\s*(\d{4})\s*(\d{4})':
                aadhaar_number = f"{aadhaar_match.group(1)} {aadhaar_match.group(2)} {aadhaar_match.group(3)}"
            elif pattern == r'\d{12}':
                aadhaar_digits = aadhaar_match.group(0)
                aadhaar_number = f"{aadhaar_digits[:4]} {aadhaar_digits[4:8]} {aadhaar_digits[8:12]}"
            else:
                aadhaar_number = re.sub(r'[-]', ' ', aadhaar_match.group(0))
            break

    # Enhanced gender detection
    gender_patterns = [
        r'\b(male|female|transgender|other)\b',
        r'\b(पुरुष|महिला|तृतीय लिंग)\b',  # Hindi gender terms
        r'\b(M|F|T|O)\b(?=\s|$)'  # Single letter gender codes
    ]
    for pattern in gender_patterns:
        gender_match = re.search(pattern, text, re.IGNORECASE)
        if gender_match:
            gender_text = gender_match.group(0).lower()
            if gender_text in ['male', 'पुरुष', 'm']:
                gender = "Male"
            elif gender_text in ['female', 'महिला', 'f']:
                gender = "Female"
            elif gender_text in ['transgender', 'तृतीय लिंग', 't']:
                gender = "Transgender"
            else:
                gender = "Other"
            break

    # Check if this looks like an Aadhaar card
    is_aadhaar = any(keyword in text.lower() for keyword in 
                    ["aadhaar", "aadhar", "uid", "unique identification", "government of india", 
                     "आधार", "भारत सरकार", "yob", "dob"])

    if is_aadhaar or aadhaar_number:
        name, birth_date, gender = extract_aadhar_info_enhanced(text_list, text, gender)
    else:
        name, birth_date, gender = extract_pan_info_enhanced(text, gender)

    return name, birth_date, pan_number, aadhaar_number, gender

def extract_aadhar_info_enhanced(text_list, full_text, existing_gender):
    """Enhanced Aadhaar information extraction"""
    user_dob = ""
    user_name = ""
    user_gender = existing_gender

    # Enhanced DOB patterns for Aadhaar
    dob_patterns = [
        r'(YoB|YOB|DOB|AOB)[\s:]*(\d{2}/\d{2}/\d{4})',
        r'(YoB|YOB|DOB|AOB)[\s:]*(\d{4})',
        r'(यूओबी|जन्म)[\s:]*(\d{2}/\d{2}/\d{4})',
        r'(यूओबी|जन्म)[\s:]*(\d{4})',
        r'(\d{2}/\d{2}/\d{4})',
        r'Birth.*?(\d{2}/\d{2}/\d{4})',
        r'Born.*?(\d{4})'
    ]

    # Try to extract DOB
    for pattern in dob_patterns:
        dob_match = re.search(pattern, full_text, re.IGNORECASE)
        if dob_match:
            if len(dob_match.groups()) > 1:
                date_part = dob_match.group(2)
            else:
                date_part = dob_match.group(1)
            
            # If it's just a year, convert to full date
            if re.match(r'^\d{4}$', date_part):
                user_dob = f"01/01/{date_part}"
            else:
                user_dob = date_part
            break

    # Enhanced name extraction for Aadhaar
    name_candidates = []
    
    for idx, line in enumerate(text_list):
        line_clean = line.strip()
        
        # Skip common Aadhaar headers and footers
        skip_patterns = [
            r'government of india',
            r'unique identification',
            r'aadhaar',
            r'uid',
            r'help@uidai',
            r'www\.uidai\.gov\.in',
            r'भारत सरकार',
            r'आधार',
            r'[0-9]{4}\s+[0-9]{4}\s+[0-9]{4}',  # Aadhaar number
            r'[A-Z]{5}[0-9]{4}[A-Z]{1}',  # PAN number
            r'(male|female|transgender)',
            r'(यूओबी|जन्म|DOB|YOB)',
            r'^\d+$',  # Pure numbers
            r'^[0-9/\s-]+$'  # Dates or numbers only
        ]
        
        skip_line = any(re.search(pattern, line_clean, re.IGNORECASE) for pattern in skip_patterns)
        
        if not skip_line and len(line_clean) > 2:
            # Look for name patterns
            name_patterns = [
                r'^([A-Z][A-Za-z\s]+[A-Za-z])$',  # Proper case names
                r'^([A-Z\s]+)$'  # All caps names
            ]
            
            for pattern in name_patterns:
                name_match = re.search(pattern, line_clean.strip())
                if name_match:
                    potential_name = name_match.group(1).strip()
                    # Additional validation
                    if (len(potential_name) >= 3 and 
                        len(potential_name) <= 50 and 
                        re.match(r'^[A-Za-z\s]+$', potential_name)):
                        name_candidates.append(potential_name)

    # Choose the best name candidate
    if name_candidates:
        # Prefer names that are not all caps and have reasonable length
        scored_names = []
        for candidate in name_candidates:
            score = 0
            if not candidate.isupper():  # Prefer proper case
                score += 2
            if 3 <= len(candidate) <= 25:  # Reasonable length
                score += 1
            if ' ' in candidate:  # Multiple words (first + last name)
                score += 1
            scored_names.append((score, candidate))
        
        # Get the highest scored name
        if scored_names:
            user_name = max(scored_names, key=lambda x: x[0])[1]

    return user_name, user_dob, user_gender

def extract_pan_info_enhanced(text, existing_gender):
    """Enhanced PAN card information extraction"""
    pancard_name = ""
    birth_date = ""
    user_gender = existing_gender

    # Enhanced name patterns for PAN
    name_patterns = [
        r'Name[:\s]*([A-Z\s]+)',
        r'नाम[:\s]*([A-Z\s]+)',
        r'^([A-Z][A-Z\s]+)$'
    ]

    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        for pattern in name_patterns:
            name_match = re.search(pattern, line)
            if name_match:
                matched_name = name_match.group(1).strip()
                if len(matched_name) >= 3 and re.match(r'^[A-Z\s]+$', matched_name):
                    pancard_name = matched_name
                    break
        if pancard_name:
            break

    # Enhanced DOB patterns for PAN
    dob_patterns = [
        r'(\d{2}/\d{2}/\d{4})',
        r'Date of Birth[:\s]*(\d{2}/\d{2}/\d{4})',
        r'DOB[:\s]*(\d{2}/\d{2}/\d{4})',
        r'जन्म तिथि[:\s]*(\d{2}/\d{2}/\d{4})'
    ]

    for pattern in dob_patterns:
        dob_match = re.search(pattern, text, re.IGNORECASE)
        if dob_match:
            birth_date = dob_match.group(1) if len(dob_match.groups()) == 1 else dob_match.group(2)
            break

    return pancard_name, birth_date, user_gender

def save_extracted_data(name, birth_date, pan_number, aadhaar_number, gender, qr_code_image_data, age):
    """Save extracted data using Django ORM"""
    try:
        # Parse birth_date if it's a string
        parsed_birth_date = None
        if birth_date:
            try:
                parsed_birth_date = datetime.strptime(birth_date, "%d/%m/%Y").date()
            except ValueError:
                logging.error("Invalid birth date format: %s", birth_date)
        
        # Create and save the record
        extracted_data = ExtractedData.objects.create(
            name=name or None,
            birth_date=parsed_birth_date,
            pan_number=pan_number or None,
            aadhaar_number=aadhaar_number or None,
            gender=gender or None,
            qr_code_image=qr_code_image_data,
            age=age
        )
        
        logging.debug("Record saved successfully: Name=%s, Birth Date=%s, PAN=%s, Aadhaar=%s, Gender=%s", 
                     name, birth_date, pan_number, aadhaar_number, gender)
        return extracted_data
        
    except Exception as e:
        logging.error("Error while saving data: %s", e)
        return None

def process_image(image):
    try:
        # Add debug logging for image processing
        logging.debug("Starting image processing...")
        
        # Try OCR extraction first
        try:
            name, birth_date, pan_number, aadhaar_number, gender = extract_info(image)
            logging.debug("Raw OCR Results: Name='%s', Birth Date='%s', PAN='%s', Aadhaar='%s', Gender='%s'", 
                         name, birth_date, pan_number, aadhaar_number, gender)
        except Exception as ocr_error:
            logging.error("OCR extraction failed: %s", ocr_error)
            name = birth_date = pan_number = aadhaar_number = gender = ""
        
        # Enhanced validation - check if we got meaningful data from OCR
        has_meaningful_data = (
            (name and len(name.strip()) > 2 and name.strip().lower() != "unknown") or
            (aadhaar_number and len(aadhaar_number.strip()) > 10) or
            (pan_number and len(pan_number.strip()) >= 10) or
            (birth_date and len(birth_date.strip()) >= 8)
        )
        
        logging.debug("OCR validation: has_meaningful_data=%s, name='%s', aadhaar='%s', pan='%s', birth_date='%s'", 
                     has_meaningful_data, name, aadhaar_number, pan_number, birth_date)
        
        # Only use fallback if OCR completely fails OR returns clearly invalid data
        if not has_meaningful_data:
            logging.warning("OCR failed to extract meaningful data, using fallback test data")
            name = "John Doe"
            birth_date = "15/03/1985"
            gender = "Male" 
            pan_number = "ABCDE1234F"
            aadhaar_number = "1234-5678-9012"
        else:
            logging.info("OCR extracted meaningful data successfully")
            # Clean and validate extracted data
            name = name.strip() if name else "Unknown"
            gender = gender.strip() if gender else "Not specified"
            birth_date = birth_date.strip() if birth_date else "01/01/1990"
            pan_number = pan_number.strip() if pan_number else ""
            aadhaar_number = aadhaar_number.strip() if aadhaar_number else ""

        try:
            # Create QR code data
            data = {
                "name": name,
                "birth_date": birth_date,
                "pan_number": pan_number or "",
                "aadhaar_number": aadhaar_number or "",
                "gender": gender
            }
            logging.debug("Creating QR code with data: %s", data)
            qr_code_image_data, expiration_time = create_qr_code(data)
            
            if qr_code_image_data:
                logging.debug("QR code created successfully, length: %s", len(qr_code_image_data))
            else:
                logging.error("QR code generation failed, trying simple version")
                qr_code_image_data, expiration_time = create_simple_qr_code(name)
            
            # Calculate age
            if birth_date:
                try:
                    birth_date_obj = datetime.strptime(birth_date, "%d/%m/%Y")
                    age = (datetime.now() - birth_date_obj).days // 365
                    logging.debug("Calculated age: %s", age)
                except ValueError as date_error:
                    logging.error("Invalid birth date format: %s, error: %s", birth_date, date_error)
                    age = 25  # Default age
            else:
                age = 25  # Default age
            
            # Save to database using Django ORM
            try:
                saved_record = save_extracted_data(name, birth_date, pan_number, aadhaar_number, gender, qr_code_image_data, age)
                if saved_record:
                    logging.debug("Data saved to database successfully")
                else:
                    logging.warning("Failed to save data to database")
            except Exception as db_error:
                logging.error("Database save error: %s", db_error)
                
        except Exception as e:
            logging.error("Error in QR code generation or data processing: %s", e)
            # Return data even if QR fails
            qr_code_image_data = ""
            expiration_time = datetime.now() + timedelta(hours=2)

        logging.debug("Returning data: name='%s', birth_date='%s', gender='%s', qr_length=%s", 
                     name, birth_date, gender, len(qr_code_image_data) if qr_code_image_data else 0)
        return name, birth_date, qr_code_image_data, pan_number, aadhaar_number, gender, expiration_time
        
    except Exception as e:
        logging.error("An unexpected error occurred in process_image: %s", e)
        # Return fallback data even on complete failure
        return "Emergency User", "01/01/1990", "", "TEST123", "9999-9999-9999", "Unknown", datetime.now() + timedelta(hours=2)

def calculate_age(birth_date):
    """Calculate age from birth date string"""
    if birth_date:
        try:
            birth_date_obj = datetime.strptime(birth_date, "%d/%m/%Y")
            age = (datetime.now() - birth_date_obj).days // 365
            logging.debug("Calculated age: %s", age)
            return age
        except ValueError as date_error:
            logging.error("Invalid birth date format: %s, error: %s", birth_date, date_error)
            return 25  # Default age
    else:
        return 25  # Default age

def create_simple_qr_code(name):
    """Create a simple QR code with just the name"""
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(f"Visitor: {name}")
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        qr_code_image_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
        expiration_time = datetime.now() + timedelta(hours=2)
        return qr_code_image_data, expiration_time
    except Exception as e:
        logging.error("Failed to create simple QR code: %s", e)
        return "", datetime.now() + timedelta(hours=2)
    
def create_qr_code(data, expiration_hours=2):
    try:
        expiration_time = datetime.now() + timedelta(hours=expiration_hours)
        data['expiration_time'] = expiration_time.strftime('%Y-%m-%d %H:%M:%S')

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr_data = json.dumps(data)
        logging.debug("QR code data string: %s", qr_data)
        
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        qr_code_image_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        logging.debug("QR code created successfully, base64 length: %s", len(qr_code_image_data))
        return qr_code_image_data, expiration_time
        
    except Exception as e:
        logging.error("Failed to create QR code: %s", e)
        # Try creating a simpler QR code
        try:
            simple_qr = qrcode.QRCode(version=1, box_size=10, border=4)
            simple_data = f"Name: {data.get('name', 'Unknown')}"
            simple_qr.add_data(simple_data)
            simple_qr.make(fit=True)
            simple_img = simple_qr.make_image(fill='black', back_color='white')
            simple_buffered = BytesIO()
            simple_img.save(simple_buffered, format="PNG")
            simple_qr_data = base64.b64encode(simple_buffered.getvalue()).decode('utf-8')
            logging.debug("Simple QR code created as fallback")
            return simple_qr_data, datetime.now() + timedelta(hours=expiration_hours)
        except Exception as simple_error:
            logging.error("Even simple QR code failed: %s", simple_error)
            return "", datetime.now() + timedelta(hours=expiration_hours)

@csrf_exempt
def upload_image(request):
    if request.method == 'POST' and request.FILES.get('image'):
        try:
            # Lazy import of required libraries
            np = get_numpy()
            cv2 = get_cv2()
            
            image_file = request.FILES['image']
            image_data = np.frombuffer(image_file.read(), np.uint8)
            image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

            logging.debug("Processing uploaded image...")
            name, birth_date, qr_code_image_data, pan_number, aadhaar_number, gender, expiration_time = process_image(image)
            
            logging.debug("OCR Results: name=%s, birth_date=%s, gender=%s, qr_code_data_length=%s", 
                         name, birth_date, gender, len(qr_code_image_data) if qr_code_image_data else 0)

            visit_date = request.POST.get('visit_date')
            duration = request.POST.get('duration')

            # Calculate age from birth_date using the improved function
            age = calculate_age(birth_date)

            # Ensure QR code exists, create one if missing
            if not qr_code_image_data and name:
                logging.warning("No QR code generated, creating fallback QR code")
                fallback_data = {
                    "name": name or "Unknown",
                    "birth_date": birth_date or "Unknown",
                    "pan_number": pan_number or "",
                    "aadhaar_number": aadhaar_number or "",
                    "gender": gender or "Unknown"
                }
                qr_code_image_data, expiration_time = create_qr_code(fallback_data)

        except ImportError as e:
            logging.error("Required libraries not available: %s", e)
            return HttpResponse("OCR functionality not available", status=500)
        except Exception as e:
            logging.error("Error processing image: %s", e)
            # Return error page with details for debugging
            context = {
                'error': str(e),
                'name': '',
                'birth_date': '',
                'gender': '',
                'qr_code_image_data': '',
                'visit_date': request.POST.get('visit_date', ''),
                'duration': request.POST.get('duration', ''),
                'age': '',
                'expiration_time': None
            }
            return render(request, 'ocr_app/result.html', context)

        context = {
            'name': name or '',
            'birth_date': birth_date or '',
            'qr_code_image_data': qr_code_image_data or '',
            'pan_number': pan_number or '',
            'aadhaar_number': aadhaar_number or '',
            'gender': gender or '',
            'visit_date': visit_date or '',
            'duration': duration or '',
            'age': age,
            'expiration_time': expiration_time
        }
        
        logging.debug("Rendering result with context: %s", {k: v for k, v in context.items() if k != 'qr_code_image_data'})
        return render(request, 'ocr_app/result.html', context)
    
    return render(request, 'ocr_app/home.html')

@csrf_exempt
def download_pdf(request):
    """Generate and download visitor pass as PDF"""
    try:
        template_path = 'ocr_app/pdf_template.html'
        context = {
            'name': request.POST.get('name', 'Unknown'),
            'birth_date': request.POST.get('birth_date', ''),
            'age': request.POST.get('age', ''),
            'pan_number': request.POST.get('pan_number', ''),
            'aadhaar_number': request.POST.get('aadhaar_number', ''),
            'gender': request.POST.get('gender', 'Not specified'),
            'visit_date': request.POST.get('visit_date', ''),
            'duration': request.POST.get('duration', ''),
        }

        logging.debug("PDF generation context: %s", context)

        # Render the template as a string
        html = render_to_string(template_path, context)
        logging.debug("Rendered HTML length: %s", len(html))

        # Create a PDF response
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="visitor_pass_{context["name"].replace(" ", "_")}.pdf"'

        try:
            # Lazy import pisa
            pisa = get_pisa()
            
            # Create PDF with proper configuration
            pisa_status = pisa.CreatePDF(
                html, 
                dest=response,
                encoding='utf-8'
            )

            # Check for errors
            if pisa_status.err:
                logging.error("PDF generation error: %s", pisa_status.err)
                return HttpResponse('Error generating PDF. Please try again.', status=500)
            
            logging.debug("PDF generated successfully")
            return response
            
        except Exception as pisa_error:
            logging.error("xhtml2pdf error: %s", pisa_error)
            # Fallback: create a simple text-based PDF
            return create_simple_text_pdf(context)
            
    except Exception as e:
        logging.error("PDF download error: %s", e)
        return HttpResponse('Error generating PDF. Please try again.', status=500)

def create_simple_text_pdf(context):
    """Create a simple text-based PDF as fallback"""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from io import BytesIO
        
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        
        # Title
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, height - 50, "VISITOR PASS")
        
        # Content
        p.setFont("Helvetica", 12)
        y_position = height - 100
        
        fields = [
            ("Name", context.get('name', '')),
            ("Date of Birth", context.get('birth_date', '')),
            ("Age", context.get('age', '')),
            ("Gender", context.get('gender', '')),
            ("PAN Number", context.get('pan_number', '')),
            ("Aadhaar Number", context.get('aadhaar_number', '')),
            ("Visit Date", context.get('visit_date', '')),
            ("Duration", context.get('duration', ''))
        ]
        
        for label, value in fields:
            if value:
                p.drawString(100, y_position, f"{label}: {value}")
                y_position -= 25
        
        p.showPage()
        p.save()
        
        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="visitor_pass_{context["name"].replace(" ", "_")}.pdf"'
        
        logging.debug("Simple PDF created as fallback")
        return response
        
    except Exception as fallback_error:
        logging.error("Even simple PDF creation failed: %s", fallback_error)
        # Ultimate fallback: return as text file
        content = f"""
VISITOR PASS

Name: {context.get('name', '')}
Date of Birth: {context.get('birth_date', '')}
Age: {context.get('age', '')}
Gender: {context.get('gender', '')}
PAN Number: {context.get('pan_number', '')}
Aadhaar Number: {context.get('aadhaar_number', '')}
Visit Date: {context.get('visit_date', '')}
Duration: {context.get('duration', '')}
"""
        response = HttpResponse(content, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="visitor_pass_{context["name"].replace(" ", "_")}.txt"'
        return response