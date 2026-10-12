// Transpose the matrix
for (int i = 0; i < n; ++i) {
    for (int j = i + 1; j < n; ++j) {
        std::swap(matrix[i][j], matrix[j][i]);
    }
}

// Reverse the rows
for (int i = 0; i < n; ++i) {
    for (int j = 0; j < n / 2; ++j) {
        std::swap(matrix[i][j], matrix[i][n - j - 1]);
    }
}