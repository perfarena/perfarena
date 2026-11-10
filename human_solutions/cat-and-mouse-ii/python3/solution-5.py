def largest_rectangle_area(matrix):
    result = 0
    Max = [0] * len(matrix[0])

    for row in matrix:
        for index, value in enumerate(row):
            Max[index] = 0 if value == 0 else Max[index] + 1

        sorted_Max = sorted(Max)
        for i, val in enumerate(sorted_Max):
            result = max(result, val * (len(sorted_Max) - i))

    return result

# Example Usage
matrix = [[1, 0, 1], [1, 1, 1], [1, 1, 0]]
print(largest_rectangle_area(matrix))  # Output: 4