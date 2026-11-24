// Define dimensions (rows and columns) of the matrix
int m = matrix.length;
int n = matrix[0].length;

// Declare booleans to track if the first row and column should be zeroed
boolean zeroFirstRow = false;
boolean zeroFirstColumn = false;

// Check for zeros in the first row and mark if necessary
for (int j = 0; j < n; j++) {
    if (matrix[0][j] == 0) {
        zeroFirstRow = true;
        break;
    }
}

// Check for zeros in the first column and mark if necessary
for (int i = 0; i < m; i++) {
    if (matrix[i][0] == 0) {
        zeroFirstColumn = true;
        break;
    }
}

// Traverse the matrix to mark rows and columns to be zeroed
for (int i = 1; i < m; i++) {
    for (int j = 1; j < n; j++) {
        if (matrix[i][j] == 0) {
            matrix[i][0] = 0; // Mark the row
            matrix[0][j] = 0; // Mark the column
        }
    }
}

// Zero out rows based on markings
for (int i = 1; i < m; i++) {
    if (matrix[i][0] == 0) {
        for (int j = 1; j < n; j++) {
            matrix[i][j] = 0;
        }
    }
}

// Zero out columns based on markings
for (int j = 1; j < n; j++) {
    if (matrix[0][j] == 0) {
        for (int i = 1; i < m; i++) {
            matrix[i][j] = 0;
        }
    }
}

// Zero out the first row if needed
if (zeroFirstRow) {
    for (int j = 0; j < n; j++) {
        matrix[0][j] = 0;
    }
}

// Zero out the first column if needed
if (zeroFirstColumn) {
    for (int i = 0; i < m; i++) {
        matrix[i][0] = 0;
    }
}