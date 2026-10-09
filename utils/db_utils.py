# utils/db_utils.py
import pyodbc
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def validate_arcsde_versioned_data(
    server: str,
    database: str,
    version_name: str,
    table_name: str,
    expected_count: Optional[int] = None,
    schema: str = 'dbo'
) -> int:
    """
    Validasi data dari ArcSDE versioned view
    
    Args:
        server: SQL Server host
        database: Database name (e.g., KPN_Plantation)
        version_name: ArcSDE version to use
        table_name: View/table name (e.g., BRD1_evw)
        expected_count: Optional expected count for validation
        schema: Schema name (default: dbo)
    
    Returns:
        int: Total row count
    
    Raises:
        Exception: If validation fails or connection error
    """
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"Trusted_Connection=yes;"
    )
    
    try:
        with pyodbc.connect(conn_str, autocommit=False) as conn:
            with conn.cursor() as cursor:
                # Step 1: Set ArcSDE version
                logger.info(f"Setting ArcSDE version: {version_name}")
                cursor.execute(f"""
                    EXEC {database}.sde.set_current_version ?
                """, (version_name,))
                
                # Step 2: Get row count
                query = f"SELECT COUNT(*) as total FROM {schema}.{table_name}"
                logger.info(f"Executing count query: {query}")
                cursor.execute(query)
                row = cursor.fetchone()
                db_count = row[0] if row else 0
                
                logger.info(f"Database count: {db_count}")
                
                # Step 3: Validate if expected count provided
                if expected_count is not None:
                    if db_count != expected_count:
                        raise ValueError(
                            f"Row count mismatch: DB={db_count}, Expected={expected_count}"
                        )
                    logger.info("Validation passed: Row counts match")
                
                return db_count
                
    except pyodbc.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise Exception(f"ArcSDE validation failed: {str(e)}")
    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        raise