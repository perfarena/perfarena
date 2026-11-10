def hasCycle(head):
    if not head or not head.next:
        return False
    
    visited = set()
    current = head
    
    while current:
        if current in visited:
            return True
        visited.add(current)
        current = current.next
    
    return False