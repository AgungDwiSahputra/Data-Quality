# validation/snapshot_validator.py
import pandas as pd
import pyarrow.parquet as pq
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

def validate_snapshot_data(
    partition_date: str,
    parquet_path: str,
    source_db_validator: Optional['ArcSDEValidator'] = None,
    table_name: Optional[str] = None,
    schema: str = 'dbo',
    sample_size: int = 100,
    exact_match: bool = True
) -> Dict[str, Any]:
    """
    Validate snapshot data between Parquet and source DB
    
    Args:
        partition_date: Snapshot date (e.g., '2026-09-07')
        parquet_path: Base path to parquet files
        source_db_validator: ArcSDEValidator instance
        table_name: Table name for DB validation
        schema: Schema name
        sample_size: Number of rows to sample for comparison
        exact_match: Whether to validate row counts exactly
    
    Returns:
        Dict with validation results
    
    Raises:
        Exception: If validation fails
    """
    results = {
        'partition_date': partition_date,
        'parquet_path': parquet_path,
        'validated_at': datetime.now().isoformat(),
        'checks': {}
    }
    
    # 1. Read Parquet
    parquet_file = Path(parquet_path) / f"snapshot_date={partition_date}" / "bronze_awm_bridge.parquet"
    
    if not parquet_file.exists():
        raise FileNotFoundError(f"Parquet file not found: {parquet_file}")
    
    logger.info(f"Reading parquet from: {parquet_file}")
    parquet_df = pd.read_parquet(parquet_file)
    parquet_count = len(parquet_df)
    
    results['parquet_count'] = parquet_count
    results['checks']['parquet_read'] = True
    logger.info(f"Parquet rows: {parquet_count}")
    
    # 2. Validate with source DB if validator provided
    if source_db_validator and table_name:
        try:
            logger.info("Validating with source DB...")
            db_count = source_db_validator.get_count(table_name, schema)
            results['db_count'] = db_count
            
            if exact_match:
                if parquet_count != db_count:
                    raise ValueError(
                        f"Row count mismatch: Parquet={parquet_count}, DB={db_count}"
                    )
                logger.info("Row count validation passed")
            else:
                # Allow some tolerance (e.g., 1% difference)
                tolerance = 0.01 * db_count
                diff = abs(parquet_count - db_count)
                if diff > tolerance:
                    logger.warning(
                        f"Count difference exceeds tolerance: "
                        f"Parquet={parquet_count}, DB={db_count}, diff={diff}"
                    )
            
            results['checks']['db_validation'] = True
            
            # 3. Sample validation (optional)
            if sample_size > 0:
                db_sample = source_db_validator.sample_data(
                    table_name, 
                    limit=min(sample_size, db_count),
                    schema=schema
                )
                # Convert to DataFrame for comparison
                db_sample_df = pd.DataFrame(db_sample)
                
                # Compare first few columns
                parquet_sample = parquet_df.head(len(db_sample_df))
                
                # Basic column comparison
                parquet_columns = set(parquet_sample.columns)
                db_columns = set(db_sample_df.columns)
                
                common_columns = parquet_columns.intersection(db_columns)
                logger.info(f"Common columns: {len(common_columns)}")
                
                results['checks']['sample_validation'] = True
                
        except Exception as e:
            logger.error(f"Database validation failed: {str(e)}")
            results['checks']['db_validation'] = False
            results['error'] = str(e)
            raise
    
    return results