def dailyTemperatures(temperatures):
    n = len(temperatures)
    stack = []
    result = [0] * n

    for j in range(n - 1, -1, -1):
        while stack and temperatures[j] >= temperatures[stack[-1]]:
            stack.pop()

        if stack:
            result[j] = stack[-1] - j

        stack.append(j)

    return result

# Test the function
temperatures = [73, 74, 75, 71, 69, 72, 76, 73]
result = dailyTemperatures(temperatures)
print(result)