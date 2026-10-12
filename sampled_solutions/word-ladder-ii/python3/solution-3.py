from collections import deque

def bfs(begin_word, end_word):
    queue = deque([(begin_word, 1)])
    visited = set([begin_word])

    while queue:
        node, steps = queue.popleft()

        if node == end_word:
            return steps

        for next_node in graph[node]:
            if next_node not in visited:
                visited.add(next_node)
                queue.append((next_node, steps + 1))

    return 0

result = bfs(begin_word, end_word)
print(result)