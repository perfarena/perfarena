def rotate_rotator(grid):
    grid.reverse()
    for i in range(len(grid)):
        for j in range(i):
            grid[i][j], grid[j][i] = grid[j][i], grid[i][j]

# Example usage
grid = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
rotate_rotator(grid)
print(grid)