from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
import boto3
import os
import uuid
from PIL import Image
import io
import base64
import json
import requests
import time
from functools import wraps

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# AWS clients
s3_client = boto3.client('s3')
rekognition_client = boto3.client('rekognition', region_name='us-east-1')
bedrock_client = boto3.client('bedrock-runtime', region_name='us-east-1')
cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

# Configuration
S3_BUCKET = os.environ.get('S3_BUCKET', 'your-image-bucket')

# Custom metrics helper
def send_custom_metric(metric_name, value, unit='Count'):
    """Send custom metric to CloudWatch"""
    try:
        cloudwatch.put_metric_data(
            Namespace='ImageProcessing/Custom',
            MetricData=[
                {
                    'MetricName': metric_name,
                    'Value': value,
                    'Unit': unit,
                    'Timestamp': time.time()
                }
            ]
        )
        print(f" Sent metric {metric_name}: {value}")
    except Exception as e:
        print(f" Failed to send metric {metric_name}: {e}")

def monitor_endpoint(endpoint_name):
    """Decorator to monitor endpoint performance"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                # Success metric
                send_custom_metric(f'{endpoint_name}_Success', 1)
                return result
            except Exception as e:
                # Error metric
                send_custom_metric(f'{endpoint_name}_Error', 1)
                send_custom_metric('ProcessingErrors', 1)
                raise
            finally:
                # Response time metric
                duration = time.time() - start_time
                send_custom_metric(f'{endpoint_name}_ResponseTime', duration * 1000, 'Milliseconds')
        return wrapper
    return decorator

@app.route('/')
@monitor_endpoint('HomePage')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST', 'OPTIONS'])
@monitor_endpoint('ImageUpload')
def upload_image():
    if request.method == 'OPTIONS':
        # Handle CORS preflight
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    print(f"Upload request received - Files: {list(request.files.keys())}")
    print(f"S3_BUCKET: {S3_BUCKET}")
    try:
        if 'image' not in request.files:
            return jsonify({'error': 'No image file provided'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Please upload JPG, JPEG, or PNG'}), 400
        
        # Generate unique filename
        file_id = str(uuid.uuid4())
        file_extension = file.filename.rsplit('.', 1)[1].lower()
        s3_key = f"uploads/{file_id}.{file_extension}"
        
        # Read and validate file content
        file_content = file.read()
        file.seek(0)
        
        # Validate image format using PIL
        try:
            from PIL import Image
            import io
            
            # Open and validate the image
            image = Image.open(io.BytesIO(file_content))
            image.verify()  # Verify it's a valid image
            
            # Reopen for processing (verify() closes the image)
            image = Image.open(io.BytesIO(file_content))
            
            # Convert to RGB if necessary (removes transparency, fixes format issues)
            if image.mode in ('RGBA', 'LA', 'P'):
                print(f"Converting {image.mode} image to RGB for Rekognition compatibility")
                rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                rgb_image.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
                image = rgb_image
            
            # Save as JPEG for better Rekognition compatibility
            output_buffer = io.BytesIO()
            image.save(output_buffer, format='JPEG', quality=95)
            processed_content = output_buffer.getvalue()
            
            # Update file extension to .jpg
            s3_key = f"uploads/{file_id}.jpg"
            content_type = 'image/jpeg'
            
        except Exception as img_error:
            print(f"Image validation/conversion error: {str(img_error)}")
            # Fall back to original file if conversion fails
            processed_content = file_content
            content_type = file.content_type or 'image/jpeg'
        
        # Upload processed image to S3
        print(f"Uploading to S3 - Bucket: {S3_BUCKET}, Key: {s3_key}")
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=processed_content,
            ContentType=content_type
        )
        print(f"S3 upload successful: {s3_key}")
        
        # Detect ingredients only
        ingredients = detect_ingredients(s3_key)
        
        # Send custom metrics
        send_custom_metric('IngredientDetections', 1)
        send_custom_metric('IngredientsFound', len(ingredients))
        
        return jsonify({
            'success': True,
            'file_id': file_id,
            's3_key': s3_key,
            'ingredients': ingredients
        })
        
    except Exception as e:
        print(f"Upload error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def detect_ingredients(s3_key):
    try:
        print(f"Starting Rekognition analysis for: {s3_key}")
        print(f"Using bucket: {S3_BUCKET}")
        
        # Use Rekognition to detect labels
        response = rekognition_client.detect_labels(
            Image={'S3Object': {'Bucket': S3_BUCKET, 'Name': s3_key}},
            MaxLabels=100,
            MinConfidence=40
        )
        
        print(f"Rekognition detected {len(response['Labels'])} labels")
        print("ALL DETECTED LABELS:")
        for label in response['Labels']:
            print(f"  - {label['Name']}: {label['Confidence']:.1f}%")
        
        # High confidence items (main dishes/prepared foods)
        main_dishes = [
            'chili', 'curry', 'pasta', 'cornbread',
            'rice', 'noodles', 'spaghetti', 'ramen', 'broth', 'chicken soup', 'tomato soup',
            'vegetable soup', 'bean soup'
        ]
        
        # Pantry items - need higher confidence to avoid false positives
        pantry_items = [
            'sauce', 'oil', 'vinegar', 'flour', 'beans', 'corn', 'peas', 
            'crackers', 'cookies', 'coffee', 'oats', 'peanut butter', 'cereal',
            'jam', 'honey', 'maple syrup', 'ketchup', 'rice', 'lentils', 'sugar', 'tomato soup', 'broth',
            'ramen', 'spaghetti'
        ]
        
        # Generic seasonings - need very high confidence
        seasonings = ['sugar', 'salt', 'pepper', 'paprika', 'basil', 'chili powder']
        
        # Fresh items - moderate confidence needed  
        fresh_items = [
            'apple', 'artichoke', 'broccolini', 'banana', 'orange', 'lemon', 'lime', 'grape', 'strawberry', 'blueberry',
            'carrot', 'tomato', 'lettuce', 'onion', 'garlic', 'potato', 'pepper',
            'broccoli', 'spinach', 'cucumber', 'avocado', 'mango', 'pineapple',
            'cheese', 'bread', 'milk', 'egg', 'butter', 'yogurt', 'chicken', 'beef', 'ground beef',
            'nuts', 'fish', 'salmon', 'tuna', 'bell pepper', 'mayonnaise', 'turkey', 'ham', 'bacon',
            'mint', 'cauliflower', 'cabbage', 'arugula', 'pear', 'nectarine'
        ]
        
        # Detect context to adjust pantry item inclusion
        all_labels = [l['Name'].lower() for l in response['Labels']]
        has_fridge_context = any(term in all_labels for term in ['refrigerator', 'fridge'])
        has_pantry_context = any(term in all_labels for term in ['shelf', 'cabinet', 'pantry', 'cupboard']) and not has_fridge_context
        
        print(f"Context detection - Fridge: {has_fridge_context}, Pantry: {has_pantry_context}")
        print(f"All labels: {all_labels}")
        
        # Check for soup context to avoid ketchup false positives
        has_soup_labels = any(soup_term in all_labels for soup_term in ['soup', 'broth', 'tomato soup', 'chicken soup', 'vegetable soup', 'bean soup'])
        has_can_labels = any(can_term in all_labels for can_term in ['can', 'tin can', 'aluminum can'])
        
        ingredients = []
        for label in response['Labels']:
            label_name = label['Name'].lower()
            
            # Skip ketchup if soup context is detected OR if can is detected (soup cans often misidentified as ketchup)
            if 'ketchup' in label_name and (has_soup_labels or has_can_labels):
                print(f"Skipping {label['Name']} due to soup/can context - likely misidentification")
                continue
            
            # Check category and apply appropriate thresholds
            is_main_dish = any(item in label_name for item in main_dishes)
            is_pantry = any(item in label_name for item in pantry_items)
            is_seasoning = any(item in label_name for item in seasonings)
            is_fresh = any(item in label_name for item in fresh_items)
            
            print(f"Checking {label['Name']} ({label['Confidence']:.1f}%): main={is_main_dish}, pantry={is_pantry}, seasoning={is_seasoning}, fresh={is_fresh}")
            
            # Apply different confidence thresholds
            should_include = False
            if is_main_dish and label['Confidence'] >= 55:
                should_include = True
                print(f"Including main dish: {label['Name']}")
            elif is_fresh and label['Confidence'] >= 52:
                should_include = True
                print(f"Including fresh item: {label['Name']}")
            elif is_pantry:
                # Use different thresholds based on context
                pantry_threshold = 42 if has_pantry_context else 60
                if label['Confidence'] >= pantry_threshold:
                    # Skip pantry items if fridge context detected (fridge context overrides pantry context)
                    if has_fridge_context:
                        print(f"Skipping pantry item {label['Name']} due to fridge context")
                        should_include = False
                    else:
                        should_include = True
                        print(f"Including pantry item: {label['Name']} (threshold: {pantry_threshold}%)")
                else:
                    print(f"Not including pantry item {label['Name']}: {label['Confidence']:.1f}% < {pantry_threshold}% threshold")
            elif is_seasoning and label['Confidence'] >= 70:
                should_include = True
                print(f"Including seasoning: {label['Name']}")
            
            if not should_include:
                print(f"Not including {label['Name']}: confidence={label['Confidence']:.1f}%, thresholds not met")
            
            if should_include:
                # Skip containers and utensils, but allow canned soup
                skip_terms = ['jar', 'bottle', 'package', 'box', 'carton',
                             'shelf', 'appliance', 'refrigerator', 'cabinet', 'counter', 'table', 'coffee table', 'dining table',
                             'cup', 'bowl', 'saucer', 'corner', 'plate', 'dish', 'spoon', 'fork', 'dessert']
                
                # Special handling for containers with food context
                has_can = 'can' in label_name
                has_bottle = 'bottle' in label_name
                has_carton = 'carton' in label_name
                has_loaf = 'loaf' in label_name
                
                # Check for food contexts
                has_soup_context = any(soup_term in [l['Name'].lower() for l in response['Labels']] 
                                     for soup_term in ['soup','broth'])
                has_dairy_context = any(dairy_term in [l['Name'].lower() for l in response['Labels']] 
                                      for dairy_term in ['milk', 'yogurt', 'cream'])
                has_condiment_context = any(condiment_term in [l['Name'].lower() for l in response['Labels']] 
                                          for condiment_term in ['ketchup', 'mustard', 'sauce', 'dressing'])
                has_bread_context = any(bread_term in [l['Name'].lower() for l in response['Labels']] 
                                      for bread_term in ['bread', 'baguette', 'roll'])
                
                # Skip containers unless they have food context
                if has_can and not has_soup_context:
                    skip_terms.append('can')
                elif has_bottle and not has_condiment_context:
                    skip_terms.append('bottle')
                elif has_carton and not has_dairy_context:
                    skip_terms.append('carton')
                elif has_loaf and not has_bread_context:
                    skip_terms.append('loaf')
                elif has_can and has_soup_context and 'soup' in label_name:
                    pass  # Allow soup items when soup context is present
                
                if not any(skip in label_name for skip in skip_terms):
                    print(f"Adding ingredient: {label['Name']}")
                    ingredients.append({
                        'name': label['Name'],
                        'confidence': round(label['Confidence'], 1)
                    })
                else:
                    print(f"Skipping {label['Name']} due to skip terms: {[term for term in skip_terms if term in label_name]}")
        
        print(f"Found {len(ingredients)} food ingredients")
        print("FILTERED INGREDIENTS:")
        for ing in ingredients:
            print(f"  - {ing['name']}: {ing['confidence']}%")
        return ingredients
        
    except Exception as e:
        print(f'Rekognition error: {str(e)}')
        import traceback
        traceback.print_exc()
        return []


def generate_recipes(ingredients_list, use_only_detected=False):
    try:
        if not ingredients_list:
            return []
            
        ingredient_names = [ing['name'] for ing in ingredients_list]
        num_ingredients = len(ingredient_names)
        
        # Basic pantry items we assume people have
        basic_pantry = ['salt', 'pepper', 'cooking oil', 'flour', 'sugar', 'baking powder', 'baking soda']
        
        # Determine number of recipes based on ingredients
        if num_ingredients <= 2:
            max_recipes = 1
        elif num_ingredients <= 4:
            max_recipes = 2
        elif num_ingredients <= 6:
            max_recipes = 3
        else:
            max_recipes = 4
        
        # Create constraint text based on use_only_detected setting
        if use_only_detected:
            all_allowed = ingredient_names + basic_pantry
            constraint_text = f"""STRICT CONSTRAINT: You can ONLY use these exact ingredients:
{', '.join(all_allowed)}

DO NOT add any other ingredients. DO NOT use herbs, spices, or seasonings beyond salt and pepper. 
DO NOT add onions, garlic, butter, cream, cheese, or any other ingredients not listed above."""
        else:
            constraint_text = "You can use the detected ingredients plus any common cooking ingredients."
        
        prompt = f"""I have these ingredients: {', '.join(ingredient_names)}

{constraint_text}

Create {max_recipes} SEPARATE recipes. Each recipe should:
- Use ONLY 2-4 ingredients that go well together
- NOT mix all ingredients together
- Be realistic and actually tasty, not weird

EXAMPLE FORMAT:
**Apple Cinnamon Snack**
Description: A simple healthy snack.
Ingredients:
- 1 apple, sliced
- 1 tsp cinnamon
- 1 tbsp honey
Instructions:
1. Slice the apple
2. Sprinkle with cinnamon
3. Drizzle with honey
4. Serve fresh
Time: 5 minutes
Difficulty: Easy

---

Now create {max_recipes} different recipes using DIFFERENT combinations of my ingredients."""
        
        response = bedrock_client.invoke_model(
            modelId='anthropic.claude-3-haiku-20240307-v1:0',
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 800,
                'messages': [{
                    'role': 'user',
                    'content': prompt
                }]
            })
        )
        
        result = json.loads(response['body'].read())
        recipes_text = result['content'][0]['text']
        
        # Parse recipes into structured format
        recipes = parse_recipes_from_text(recipes_text)
        return recipes
        
    except Exception as e:
        print(f'Recipe generation error: {str(e)}')
        # Fallback to simple recipe
        return [{
            'name': f'Simple {ingredient_names[0] if ingredient_names else "Ingredient"} Dish',
            'description': 'A quick and easy dish using your available ingredients.',
            'ingredients': ingredient_names[:3] + ['Salt', 'Pepper', 'Olive oil'],
            'instructions': [
                'Wash and prepare ingredients',
                'Heat olive oil in a pan',
                'Cook main ingredients for 10-15 minutes',
                'Season with salt and pepper',
                'Serve hot'
            ],
            'cooking_time': '20 minutes',
            'difficulty': 'Easy'
        }]

def parse_recipes_from_text(text):
    """Parse AI-generated recipe text into structured format"""
    recipes = []
    
    if not text or len(text.strip()) < 50:
        return []
    
    # Split by common recipe separators
    recipe_blocks = []
    if '---' in text:
        recipe_blocks = text.split('---')
    elif '**' in text and text.count('**') >= 4:  # At least 2 recipe titles
        # Split by recipe titles marked with **
        parts = text.split('**')
        current_recipe = ''
        for i, part in enumerate(parts):
            if i % 2 == 1:  # Odd indices are recipe titles
                if current_recipe:
                    recipe_blocks.append(current_recipe)
                current_recipe = '**' + part + '**'
            else:
                current_recipe += part
        if current_recipe:
            recipe_blocks.append(current_recipe)
    else:
        recipe_blocks = [text]
    
    for i, block in enumerate(recipe_blocks):
        if not block.strip():
            continue
            
        lines = [line.strip() for line in block.strip().split('\n') if line.strip()]
        if not lines:
            continue
            
        recipe = {
            'name': f'Recipe {i+1}',
            'description': 'A delicious AI-generated recipe.',
            'ingredients': [],
            'instructions': [],
            'cooking_time': '30 minutes',
            'difficulty': 'Easy'
        }
        
        current_section = None
        name_found = False
        
        for line in lines:
            line_lower = line.lower()
            
            # Recipe name - look for **Title** pattern first
            if line.startswith('**') and line.endswith('**') and not name_found:
                recipe['name'] = line.replace('**', '').strip()
                name_found = True
            elif line.startswith('Description:'):
                recipe['description'] = line.replace('Description:', '').strip()
            elif 'ingredients:' in line_lower:
                current_section = 'ingredients'
            elif 'instructions:' in line_lower or 'steps:' in line_lower:
                current_section = 'instructions'
            elif 'time:' in line_lower:
                recipe['cooking_time'] = line.split(':', 1)[1].strip()
                current_section = None
            elif 'difficulty:' in line_lower:
                recipe['difficulty'] = line.split(':', 1)[1].strip()
                current_section = None
            elif line.startswith('-') and current_section == 'ingredients':
                recipe['ingredients'].append(line[1:].strip())
            elif line and line[0].isdigit() and '.' in line and current_section == 'instructions':
                # Remove the number prefix
                instruction = line.split('.', 1)[1].strip() if '.' in line else line
                recipe['instructions'].append(instruction)
            elif current_section == 'instructions' and line and not any(x in line_lower for x in ['time:', 'difficulty:', 'ingredients:']):
                recipe['instructions'].append(line)
        
        # Ensure we have meaningful content
        if recipe['ingredients'] or recipe['instructions']:
            recipes.append(recipe)
    
    # If no recipes parsed successfully, create a simple fallback
    if not recipes:
        recipes.append({
            'name': 'AI Generated Recipe',
            'description': 'A creative recipe using your ingredients.',
            'ingredients': ['Your detected ingredients', 'Salt and pepper to taste', 'Cooking oil as needed'],
            'instructions': ['Prepare all ingredients', 'Cook according to your preference', 'Season to taste', 'Serve and enjoy'],
            'cooking_time': '30 minutes',
            'difficulty': 'Easy'
        })
    
    return recipes

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/upload-multiple', methods=['POST', 'OPTIONS'])
def upload_multiple_images():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    
    try:
        print(f"Multiple upload request - All keys: {list(request.files.keys())}")
        print(f"Request files count: {len(request.files)}")
        
        files = []
        for key in request.files:
            print(f"Processing key: {key}")
            if key.startswith('image_'):
                file_obj = request.files[key]
                print(f"Found file: {file_obj.filename}")
                files.append(file_obj)
        
        print(f"Total files to process: {len(files)}")
        
        if not files:
            return jsonify({'error': 'No images provided'}), 400
        
        results = []
        for file in files:
            if not file.filename:
                continue
                
            # Generate unique filename
            file_id = str(uuid.uuid4())
            file_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            s3_key = f"uploads/{file_id}.{file_extension}"
            
            # Read and validate file content
            file_content = file.read()
            file.seek(0)
            
            # Validate image format using PIL
            try:
                # Open and validate the image
                image = Image.open(io.BytesIO(file_content))
                image.verify()  # Verify it's a valid image
                
                # Reopen for processing (verify() closes the image)
                image = Image.open(io.BytesIO(file_content))
                
                # Convert to RGB if necessary (removes transparency, fixes format issues)
                if image.mode in ('RGBA', 'LA', 'P'):
                    print(f"Converting {image.mode} image to RGB for Rekognition compatibility")
                    rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'P':
                        image = image.convert('RGBA')
                    rgb_image.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
                    image = rgb_image
                
                # Save as JPEG for better Rekognition compatibility
                output_buffer = io.BytesIO()
                image.save(output_buffer, format='JPEG', quality=95)
                processed_content = output_buffer.getvalue()
                
                # Update file extension to .jpg
                s3_key = f"uploads/{file_id}.jpg"
                content_type = 'image/jpeg'
                
            except Exception as img_error:
                print(f"Image validation/conversion error: {str(img_error)}")
                # Fall back to original file if conversion fails
                processed_content = file_content
                content_type = file.content_type or 'image/jpeg'
            
            # Upload processed image to S3
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=processed_content,
                ContentType=content_type
            )
            
            # Detect ingredients
            ingredients = detect_ingredients(s3_key)
            
            results.append({
                'file_id': file_id,
                's3_key': s3_key,
                'ingredients': ingredients
            })
        
        # Combine all ingredients
        all_ingredients = []
        for result in results:
            all_ingredients.extend(result['ingredients'])
        
        # Remove duplicates
        unique_ingredients = []
        seen_names = set()
        for ing in all_ingredients:
            if ing['name'].lower() not in seen_names:
                unique_ingredients.append(ing)
                seen_names.add(ing['name'].lower())
        
        return jsonify({
            'success': True,
            'files_processed': len(results),
            'results': results,
            'all_ingredients': unique_ingredients
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-recipe', methods=['POST', 'OPTIONS'])
@monitor_endpoint('RecipeGeneration')
def generate_recipe_endpoint():
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    
    try:
        data = request.get_json()
        ingredients = data.get('ingredients', [])
        use_only_detected = data.get('use_only_detected', False)
        
        if not ingredients:
            return jsonify({'error': 'No ingredients provided'}), 400
        
        # Get AI-generated recipes only
        ai_recipes = generate_recipes(ingredients, use_only_detected)
        
        # Add source labels
        for recipe in ai_recipes:
            recipe['source'] = 'AI-Generated'
        
        # Send custom metrics
        send_custom_metric('RecipeGenerations', 1)
        send_custom_metric('RecipesGenerated', len(ai_recipes))
        
        return jsonify({
            'success': True,
            'recipes': ai_recipes,
            'total_recipes': len(ai_recipes)
        })
        
    except Exception as e:
        print(f'Recipe endpoint error: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/test-upload', methods=['GET'])
def test_upload():
    return jsonify({
        'message': 'Upload endpoint is accessible',
        's3_bucket': S3_BUCKET,
        'use_sagemaker': USE_SAGEMAKER
    }), 200

@app.route('/debug-rekognition/<path:s3_key>', methods=['GET'])
def debug_rekognition(s3_key):
    try:
        ingredients = detect_ingredients(s3_key)
        return jsonify({
            'success': True,
            's3_key': s3_key,
            'bucket': S3_BUCKET,
            'ingredients': ingredients
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            's3_key': s3_key,
            'bucket': S3_BUCKET
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
