# jobs/bronze_awm_evw.py
import pandas as pd
import pyodbc
import logging
from datetime import datetime
import boto3
from typing import Optional

from validation.arcsde_validator import ArcSDEValidator
from validation.snapshot_validator import validate_snapshot_data
from utils.db_utils import validate_arcsde_versioned_data

logger = logging.getLogger(__name__)

def bronze_awm_evw(
    server: str = '172.21.4.71',
    database: str = 'KPN_Plantation',
    version_name: str = 'Esri_Anonymous_pub_psa/PA_Inve',
    table_name: str = 'BRD1_evw',
    s3_bucket: str = 'kpn-gis-s3-bucket',
    s3_prefix: str = 'datalake/bronze/awm/master_weir',
    partition_date: Optional[str] = None
) -> dict:
    """
    ETL job untuk bronze layer ArcSDE data
    
    Args:
        server: SQL Server host
        database: Database name
        version_name: ArcSDE version
        table_name: Source table/view
        s3_bucket: S3 bucket name
        s3_prefix: S3 prefix for storage
        partition_date: Snapshot date (default: today)
    
    Returns:
        dict: Job results with validation info
    """
    
    if not partition_date:
        partition_date = datetime.now().strftime('%Y-%m-%d')
    
    logger.info(f"Starting bronze_awm_evw job for date: {partition_date}")
    
    result = {
        'job': 'bronze_awm_evw',
        'status': 'started',
        'partition_date': partition_date,
        'rows_processed': 0,
        'validation': {}
    }
    
    try:
        # 1. Extract data from ArcSDE
        with ArcSDEValidator(server, database, version_name) as validator:
            logger.info("Connected to ArcSDE")
            
            # Get row count before extraction
            row_count = validator.get_count(table_name)
            logger.info(f"Total rows to extract: {row_count}")
            
            if row_count == 0:
                logger.warning("No data to extract")
                result['status'] = 'completed_empty'
                return result
            
            # Extract data
            query = f"SELECT * FROM dbo.{table_name}"
            data = validator.execute_query(query)
            
            # Convert to DataFrame
            columns = [desc[0] for desc in validator._cursor.description]
            df = pd.DataFrame.from_records(data, columns=columns)
            
            logger.info(f"Extracted {len(df)} rows")
            
            # 2. Load to Parquet in S3
            s3_path = f"s3://{s3_bucket}/{s3_prefix}/snapshot_date={partition_date}/bronze_awm_weir.parquet"
            
            # Write to S3
            df.to_parquet(
                s3_path,
                index=False,
                engine='pyarrow',
                compression='snappy'
            )
            
            logger.info(f"Uploaded to S3: {s3_path}")
            
            result['rows_processed'] = len(df)
            result['s3_path'] = s3_path
            
            # 3. Validate
            logger.info("Starting validation...")
            
            # Validate row count
            parquet_df = pd.read_parquet(s3_path)
            parquet_count = len(parquet_df)
            
            # Validate with source DB using the same validator
            validator.validate_count(table_name, parquet_count)
            
            result['validation']['row_count_match'] = True
            result['validation']['db_count'] = row_count
            result['validation']['parquet_count'] = parquet_count
            
            # Optional: Additional snapshot validation
            snapshot_result = validate_snapshot_data(
                partition_date=partition_date,
                parquet_path=f"s3://{s3_bucket}/{s3_prefix}",
                source_db_validator=validator,
                table_name=table_name,
                sample_size=100
            )
            
            result['validation']['snapshot'] = snapshot_result
            result['status'] = 'completed'
            
            logger.info("Job completed successfully")
            
    except Exception as e:
        logger.error(f"Job failed: {str(e)}")
        result['status'] = 'failed'
        result['error'] = str(e)
        raise
    
    return result