# KitchenLens
Repo for KitchenLens AWS project

An intelligent web application that analyzes photos of your ingredients and generates personalized recipes using AWS Rekognition and Amazon Bedrock AI.

## Features

- **Smart Ingredient Detection**: Upload photos of your fridge, pantry, or ingredients and get AI-powered ingredient identification
- **Multi-Image Support**: Upload multiple photos at once to get a comprehensive ingredient list
- **AI Recipe Generation**: Get creative, personalized recipes based on your available ingredients
- **Flexible Recipe Modes**: Choose between using only detected ingredients or allowing additional common pantry items
- **Real-time Processing**: Fast image analysis and recipe generation
- **Scalable Architecture**: Built on AWS with auto-scaling capabilities

## Architecture

### Frontend
- Flask web application with responsive HTML/CSS/JavaScript
- CORS-enabled REST API
- Multi-file upload support
- Real-time ingredient display

### AWS Services
- **Amazon Rekognition**: Computer vision for ingredient detection from images
- **Amazon Bedrock**: Claude AI model for recipe generation
- **Amazon S3**: Image storage and processing
- **Amazon ECS**: Containerized application deployment
- **Application Load Balancer**: Traffic distribution across multiple instances
- **Auto Scaling Group**: Automatic scaling based on demand
- **Amazon ECR**: Docker container registry
- **CloudWatch**: Custom metrics and monitoring

### Infrastructure
- **Multi-AZ Deployment**: High availability across two availability zones
- **Auto Scaling**: Automatic horizontal scaling based on traffic
- **Load Balancing**: Distributed traffic across healthy instances
- **Containerized**: Docker-based deployment for consistency

## How It Works

1. **Image Upload**: Users upload photos of their ingredients (fridge contents, pantry items, etc.)
2. **AI Analysis**: AWS Rekognition analyzes images to identify food items with confidence scores
3. **Smart Filtering**: Custom logic filters results to focus on actual food ingredients
4. **Recipe Generation**: Amazon Bedrock's Claude AI creates personalized recipes based on detected ingredients
5. **Recipe Delivery**: Multiple recipe options are presented with ingredients, instructions, and cooking details

## Demo Video

Check out KitchenLens in action:

![Demo Video](DemoVideo.mov)


## Key Technical Features

### Intelligent Ingredient Detection
- Context-aware filtering (distinguishes between fridge vs. pantry items)
- Confidence thresholds optimized for different food categories
- Smart container detection (avoids false positives from jars, bottles, etc.)

### Advanced Recipe Generation
- Multiple recipe variations from the same ingredients
- Difficulty and time estimation
- Structured recipe format with clear instructions
- Option to use only detected ingredients or include common pantry items

### Monitoring & Observability
- Custom CloudWatch metrics for performance tracking
- Endpoint monitoring with success/error rates
- Response time tracking
- Processing volume metrics

## Local Development Setup

### Prerequisites
- Python 3.9+
- AWS Account with appropriate permissions
- AWS CLI configured
- Docker (for containerization)

### Environment Variables
```bash
export S3_BUCKET=your-image-bucket
export AWS_DEFAULT_REGION=us-east-1
```

### Installation
```bash
# Clone the repository
git clone [your-repo-url]
cd kitchen-lens

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

The application will be available at `http://localhost:5000`


## AWS Deployment

### Infrastructure Components

1. **ECS Cluster**: Container orchestration
2. **ECR Repository**: Container image storage
3. **Application Load Balancer**: Traffic distribution
4. **Auto Scaling Group**: Dynamic scaling
5. **Target Groups**: Health checking and routing
6. **Multi-AZ EC2 Instances**: High availability

### Deployment Steps

1. **Build with Docker and push to ECR**:
```bash
# Build and tag
docker build -t kitchen-lens .
docker tag kitchen-lens:latest [account-id].dkr.ecr.us-east-1.amazonaws.com/kitchen-lens:latest

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin [account-id].dkr.ecr.us-east-1.amazonaws.com
docker push [account-id].dkr.ecr.us-east-1.amazonaws.com/kitchen-lens:latest
```

2. **Update ECS Service**: Deploy new task definition with updated image
3. **Auto Scaling**: Configured for 2 instances across 2 AZs
4. **Load Balancer**: Routes traffic on port 80 to container port 5000

## API Endpoints

### Upload Single Image
```
POST /upload
Content-Type: multipart/form-data
Body: image file

Response: {
  "success": true,
  "file_id": "uuid",
  "ingredients": [...]
}
```

### Upload Multiple Images
```
POST /upload-multiple
Content-Type: multipart/form-data
Body: multiple image files

Response: {
  "success": true,
  "all_ingredients": [...],
  "files_processed": 3
}
```

### Generate Recipes
```
POST /generate-recipe
Content-Type: application/json
Body: {
  "ingredients": [...],
  "use_only_detected": false
}

Response: {
  "success": true,
  "recipes": [...],
  "total_recipes": 3
}
```

---
