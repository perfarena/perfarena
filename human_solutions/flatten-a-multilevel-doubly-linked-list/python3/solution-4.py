for row in range(1, len(matrix)):
    if not is_diagonal_univalue(row, 0, matrix[row][0]):
        return False