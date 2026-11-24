def is_diagonal_univalue(row, column, value):
    while row < len(matrix) and column < len(matrix[0]):
        if matrix[row][column] != value:
            return False
        row += 1
        column += 1
    return True