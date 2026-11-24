from collections import Counter

def find_intersection_with_frequency(num1, num2):
    c1 = Counter(num1)
    c2 = Counter(num2)
    
    intersection = c1 & c2
    
    result = []
    for num, frequency in intersection.items():
        result.extend([num] * frequency)
    
    return result

# Test the function
num1 = [1, 2, 2, 1]
num2 = [2, 2]
print(find_intersection_with_frequency(num1, num2))  # Output: [2, 2]

num1 = [4, 9, 5]
num2 = [9, 4, 9, 8, 4]
print(find_intersection_with_frequency(num1, num2))  # Output: [9, 4, 9, 4]