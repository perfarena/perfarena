def fizzbuzz(n):
    answer = []
    for i in range(1, n + 1):
        if i % 3 == 0 and i % 5 == 0:
            answer.append("fizzbuzz")
        elif i % 3 == 0:
            answer.append("fizz")
        elif i % 5 == 0:
            answer.append("buzz")
        else:
            answer.append(str(i))
    return answer

# Test the function with n = 15
result = fizzbuzz(15)
print(result)