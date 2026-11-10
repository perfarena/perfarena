def has_cycle(head):
    for _ in range(10**4 + 2):  # Loop through a large number of times
        if head is None:
            return False  # No cycle found
        head = head.next
    return True  # Cycle detected