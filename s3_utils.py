import boto3
import os
from botocore.exceptions import ClientError
import logging

# It's recommended to configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize S3 client from environment variables
try:
    S3_BUCKET = os.environ['AWS_BUCKET_NAME']
    s3_client = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )
except KeyError as e:
    logger.error(f"Missing essential environment variable: {e}")
    s3_client = None
    S3_BUCKET = None

def generate_presigned_upload_url(object_name: str, expiration: int = 3600) -> str | None:
    """
    Generate a presigned URL to upload a file to S3 using HTTP PUT.

    :param object_name: The key (path) for the new object in the S3 bucket.
    :param expiration: Time in seconds for the presigned URL to remain valid.
    :return: The presigned URL as a string, or None if an error occurred.
    """
    if not s3_client or not S3_BUCKET:
        logger.error("S3 client or bucket is not configured.")
        return None
    try:
        response = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': S3_BUCKET, 'Key': object_name},
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        logger.error(f"Error generating presigned URL: {e}")
        return None

def download_file_from_s3(object_name: str, local_path: str) -> bool:
    """
    Download a file from an S3 bucket to a local path.

    :param object_name: The key of the object to download.
    :param local_path: The local path where the file should be saved.
    :return: True if download was successful, else False.
    """
    if not s3_client or not S3_BUCKET:
        logger.error("S3 client or bucket is not configured.")
        return False
    try:
        s3_client.download_file(S3_BUCKET, object_name, local_path)
        logger.info(f"File {object_name} downloaded successfully to {local_path}")
        return True
    except ClientError as e:
        logger.error(f"Failed to download file {object_name} from S3: {e}")
        return False

def delete_s3_prefix(prefix: str):
    """
    Deletes all objects under a given prefix (folder) in S3.

    :param prefix: The prefix to delete (e.g., 'discovery_accelerator/uploads/user/project/').
    """
    if not s3_client or not S3_BUCKET:
        logger.error("S3 client or bucket is not configured.")
        return
        
    # Ensure the prefix ends with a slash to avoid unintended deletions
    if not prefix.endswith('/'):
        prefix += '/'

    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix)

        delete_us = {'Objects': []}
        for item in pages.search('Contents'):
            if item:
                delete_us['Objects'].append({'Key': item['Key']})

        if not delete_us['Objects']:
            logger.info(f"No objects found to delete under prefix: {prefix}")
            return

        # The delete_objects API call can handle up to 1000 keys at a time.
        # For simplicity, this example assumes fewer than 1000 objects.
        # For larger numbers, you would need to batch the delete requests.
        s3_client.delete_objects(Bucket=S3_BUCKET, Delete=delete_us)
        logger.info(f"Successfully deleted objects under prefix: {prefix}")

    except ClientError as e:
        logger.error(f"Error deleting objects from S3 for prefix {prefix}: {e}")