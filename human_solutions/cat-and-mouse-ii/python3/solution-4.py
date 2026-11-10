# Find the size of the matrix
n = len(matrix)
m = len(matrix[0])

# Initialize a matrix of zeros
prefix_sum = [[0 for _ in range(m)] for _ in range(n)]

# Calculate prefix sum column-wise
for i in range(n):
    for j in range(m):
        if matrix[i][j] != 0:
            prefix_sum[i][j] += prefix_sum[i-1][j]

# Sort each row in descending order
for i in range(n):
    matrix[i].sort(reverse=True)

# Calculate maximum area for each row
answer = 0
for i in range(n):
    for j in range(m):
        height = matrix[i][j]
        width = j + 1
        area = height * width
        answer = max(answer, area)

print("The maximum area of the sub matrix is:", answer)