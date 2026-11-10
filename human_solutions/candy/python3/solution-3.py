def distribute_candies(ratings):
    n = len(ratings)
    total_candies = [1] * n

    for i in range(1, n):
        if ratings[i] > ratings[i - 1]:
            total_candies[i] = total_candies[i - 1] + 1

    for i in range(n - 2, -1, -1):
        if ratings[i] > ratings[i + 1]:
            total_candies[i] = max(total_candies[i], total_candies[i + 1] + 1)

    return sum(total_candies)

# Example usage
ratings = [2, 4, 6, 7, 3, 2, 2]
min_candies = distribute_candies(ratings)
print(min_candies)  # Output: 14