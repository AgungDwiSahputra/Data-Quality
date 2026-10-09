# validation/arcsde_validator.py
import pyodbc
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class ArcSDEValidator:
    """
    Validator khusus untuk ArcSDE versioned data
    """
    
    def __init__(
        self,
        server: str,
        database: str,
        version_name: str,
        driver: str = 'ODBC Driver 17 for SQL Server',
        timeout: int = 30
    ):
        self.server = server
        self.database = database
        self.version_name = version_name
        self.driver = driver
        self.timeout = timeout
        self._conn = None
        self._cursor = None
    
    def __enter__(self):
        self._connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close()
    
    def _connect(self):
        """Establish connection with ArcSDE version setting"""
        conn_str = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.server};"
            f"DATABASE={self.database};"
            f"Trusted_Connection=yes;"
            f"CONNECTION TIMEOUT={self.timeout};"
        )
        
        try:
            self._conn = pyodbc.connect(conn_str, autocommit=False)
            self._cursor = self._conn.cursor()
            
            # Set ArcSDE version
            logger.info(f"Setting ArcSDE version to: {self.version_name}")
            self._cursor.execute(f"""
                EXEC {self.database}.sde.set_current_version ?
            """, (self.version_name,))
            
        except Exception as e:
            raise Exception(f"Failed to connect to ArcSDE: {str(e)}")
    
    def _close(self):
        """Close connections"""
        if self._cursor:
            self._cursor.close()
        if self._conn:
            self._conn.close()
    
    def execute_query(self, query: str) -> list:
        """
        Execute query dengan version setting yang sudah aktif
        
        Args:
            query: SQL query to execute
        
        Returns:
            list: Query results
        """
        if not self._cursor:
            raise Exception("Connection not established. Use context manager.")
        
        try:
            self._cursor.execute(query)
            return self._cursor.fetchall()
        except Exception as e:
            logger.error(f"Query execution failed: {query}")
            raise Exception(f"ArcSDE query failed: {str(e)}")
    
    def get_count(self, table_name: str, schema: str = 'dbo') -> int:
        """Get row count from table/view"""
        query = f"SELECT COUNT(*) as total FROM {schema}.{table_name}"
        result = self.execute_query(query)
        return result[0][0] if result else 0
    
    def validate_count(
        self,
        table_name: str,
        expected_count: int,
        schema: str = 'dbo'
    ) -> bool:
        """
        Validate row count matches expected
        
        Returns:
            bool: True if validation passes
        
        Raises:
            ValueError: If counts don't match
        """
        actual_count = self.get_count(table_name, schema)
        
        if actual_count != expected_count:
            raise ValueError(
                f"Count mismatch for {schema}.{table_name}: "
                f"Expected={expected_count}, Actual={actual_count}"
            )
        
        logger.info(f"Validation passed: {actual_count} rows")
        return True
    
    def get_table_schema_info(self, table_name: str, schema: str = 'dbo') -> Dict[str, Any]:
        """Get table schema information"""
        query = f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                IS_NULLABLE,
                CHARACTER_MAXIMUM_LENGTH
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema}'
            AND TABLE_NAME = '{table_name}'
            ORDER BY ORDINAL_POSITION
        """
        results = self.execute_query(query)
        
        schema_info = {}
        for row in results:
            schema_info[row[0]] = {
                'data_type': row[1],
                'is_nullable': row[2] == 'YES',
                'max_length': row[3]
            }
        
        return schema_info
    
    def sample_data(
        self,
        table_name: str,
        limit: int = 1000,
        schema: str = 'dbo'
    ) -> list:
        """Get sample data from table"""
        query = f"SELECT TOP {limit} * FROM {schema}.{table_name}"
        return self.execute_query(query)