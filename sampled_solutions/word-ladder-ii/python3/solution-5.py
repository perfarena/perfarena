queue = deque([end_word])
distance[end_word] = 0

while queue:
    u = queue.popleft()
    for i in range(word_length):
        for letter in ascii_lowercase:
            new = u[:i] + letter + u[i + 1:]
            if new in word_set:
                if distance[new] == float('inf'):
                    distance[new] = distance[u] + 1
                    queue.append(new)
                    graph[new].add(u)