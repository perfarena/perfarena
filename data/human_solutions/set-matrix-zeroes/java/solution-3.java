// Create a class to store pairs of indices
class Pair {
    int i;
    int j;
    
    public Pair(int i, int j) {
        this.i = i;
        this.j = j;
    }
}

// Traverse the matrix to find zeros and store their indices
List<Pair> pairs = new ArrayList<>();
for (int i = 0; i < matrix.length; i++) {
    for (int j = 0; j < matrix[0].length; j++) {
        if (matrix[i][j] == 0) {
            pairs.add(new Pair(i, j));
        }
    }
}

// Iterate through the stored pairs and set corresponding rows and columns to zero
for (Pair pair : pairs) {
    // Set entire row to zero
    for (int k = 0; k < matrix[0].length; k++) {
        matrix[pair.i][k] = 0;
    }
    
    // Set entire column to zero
    for (int k = 0; k < matrix.length; k++) {
        matrix[k][pair.j] = 0;
    }
}