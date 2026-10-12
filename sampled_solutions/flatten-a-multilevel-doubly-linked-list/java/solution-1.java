int rows = Matrix.length;
int columns = Matrix[0].length;

for (int row = 1; row < rows; row++) {
    for (int column = 1; column < columns; column++) {
        if (Matrix[row][column] != Matrix[row - 1][column - 1]) {
            return false;
        }
    }
}
return true;