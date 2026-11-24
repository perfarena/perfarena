def min_candies(children):
    n = len(children)
    children.sort()  # Sort children based on ratings
    candies = [1] * n  # Initialize each child with one candy
    
    # Adjust candies based on ratings
    for i in range(1, n):
        if children[i] > children[i - 1]:
            candies[i] = candies[i - 1] + 1
    
    return sum(candies)  # Return total candies distributed

# Example Usage
children = [1, 5, 5, 1, 2, 3, 4, 5, 4, 3, 2, 1]
print(min_candies(children))  # Output: 18