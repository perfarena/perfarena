void setMatrixZeros(int[][] matrix) {
    int rows = matrix.length;
    int columns = matrix[0].length;
    
    Set<Integer> rowSet = new HashSet<>();
    Set<Integer> columnSet = new HashSet<>();
    
    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < columns; j++) {
            if (matrix[i][j] == 0) {
                rowSet.add(i);
                columnSet.add(j);
            }
        }
    }
    
    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < columns; j++) {
            if (rowSet.contains(i) || columnSet.contains(j)) {
                matrix[i][j] = 0;
            }
        }
    }
}