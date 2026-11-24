def distribute_candies(ratings):
    n = len(ratings)
    candy = [1] * n

    # Traverse from left to right
    for i in range(1, n):
        if ratings[i] > ratings[i - 1]:
            candy[i] = candy[i - 1] + 1

    # Traverse from right to left
    for i in range(n - 2, -1, -1):
        if ratings[i] > ratings[i + 1]:
            candy[i] = max(candy[i], candy[i + 1] + 1)

    total_candies = sum(candy)
    return total_candies

# Example usage
ratings = [2, 3, 4, 5, 2, 1]
result = distribute_candies(ratings)
print(result)