---
title: Midterm 1
instructions: Auto-generated
---

## Version A

- (50 points) 1. (50 points) Minimum score. You don’t have to do anything for this question.
  - **Correct**
    - Solution: OK
  - **Rubric**
    - Correct (+0)
    - Incorrect (-50)

- (1 points) 2. (1 point) if the 16-bit number 0x95B3 is stored big-endian, what will be the first byte in HEX at the
  - **Correct**
    - address where it is stored? (include the 0x prefix and no leading zeros. use uppercase letters) Solution: 0x95 / 95
  - **Rubric**
    - Correct (+0)
    - Incorrect (-1)

- (1 points) 3. (1 point) if the 16-bit number 0xB395 is stored little-endian, what will be the first byte in HEX at the
  - **Correct**
    - address where it is stored? (include the 0x prefix and no leading zeros. use uppercase letters) Solution: 0x95 / 95 4. (2 points) the following function clears bit (makes 0) n (where bit 0 is the least significant bit) in a 64-bit page table entry. write the code to replace XXXX (DO NOT USE ANY SPACES!) void clr_pte_bit(uint64_t *pte, n) { *pte = XXXX; } Solution: *pte&~(1< 5. (2 points) What does each level of the Multi-Level Feedback Queue (MLFQ) correspond to? Solution: Priority
  - **Rubric**
    - Correct (+0)
    - Incorrect (-1)

- (2 points) 6. (2 points) in MLFQ, rule5 says: “After some time period S, move all the jobs in the system to the topmost
  - **Correct**
    - queue.” What problem does rule 5 prevent? (one word, all lowercase) Solution: starvation / Starvation
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 7. (2 points) if we have a program with the following lines of code:
  - **Correct**
    - char buffer [1024] ; int fd = open("/tmp/file", O_RDWR); char *data = mmap(NULL, 1024*1024, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0); Assume all of the above lines and the following lines execute successfully. Write a line of code that will cause a dirty page for /tmp/file.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 8. (2 points) if we are running on a Linux machine with 4G of RAM and 1G of swap, what happens when
  - **Correct**
    - we run the following program? #include <stdio.h> #include <stdlib.h> int main(int argc, char *argv[]) { char *mem[20] ; for (int i = 0; i < 20; i++) { mem[(i] = malloc(1024*1024*1024) ; if (mem[i] == NULL) { printf("failed\n") ; exit (i+1); } + printf ("allocated 20G\n") ; exit (0);
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 9. (2 points) We have allocated memory into 6 contiguous chunks A-F.
  - **Correct**
    - The chunk sizes and how much memory they actually use: chunk | size used A 512 bytes free B 768 bytes free c 512 bytes 128 bytes D 1.5K (IK+512 bytes) free E 2K 1536 bytes E 4K 2048 bytes assuming chunks cannot be split, how much storage (in bytes) is wasted due to internal fragmentation? Solution: 2944
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 10. (2 points) We have allocated memory into 6 contiguous chunks A-F.
  - **Correct**
    - The chunk sizes and how much memory they actually use: chunk | size used A 1K 1K B 2K free c 512bytes 512 bytes D 1K free E 2K 2K E 2K free For example, chunk A is 1K in size, and all of that 1K is being used; chunk B has 2K bytes of free space that can be used for future allocations. What kind of fragmentation prevents us from allocating a 3K chunk of space? Solution: external fragmentation
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 11. (2 points) Assuming a 64-bit address space with 4K pages and 4-level page table, what is the virtual
  - **Correct**
    - page number for the address 0xC001D00D11B0? Solution: C001D00D1 / 0xc001d00d1 / c001d00d1 / 0xC001D00D1
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 12. (2 points) Assuming a 64-bit address space with 16K pages and 4-level page table, what is the virtual
  - **Correct**
    - page number for the address 0xC001D00D11B0? Solution: 0x300074034
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 13. (2 points) ona 64-bit CPU (16K page) with CR3=0xFACE8031 what would be the physical address of the
  - **Correct**
    - top level page table (P4) entry for the virtual address 0x9 A3F5C8B1A42? Solution: Oxface8008 / 0xFACE8008
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 14. (2 points) ona 64-bit CPU (16K page) with the P1 page table entry (the last one) = 0xCAFE4067 what
  - **Correct**
    - would be the physical address of 0x9A3F5C8B1A42? Solution: Oxcafe5a42 / 0xCAFE5A42
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 15. (2 points) A parent process that has a 1G array of integers calls fork(). Give an example of something
  - **Correct**
    - that a child or parent would do that would NOT show the benefits of COW (Copy-on-write)?
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 16. (2 points) What is an example of the CPU triggering a page fault, but the process continues normally?
  - **Correct**
    - No answer detected.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 17. (2 points) When do you see virtual addresses in your program?
  - **Correct**
    - No answer detected.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 18. (2 points) you have 14 bytes of memory. assume there is no overhead for your memory allocations (you
  - **Correct**
    - only need to find space for the bytes allocated). you must allocate a contiguous set of bytes: you cannot split allocations. you have the following sequence of operations on the memory: allocate 6 bytes as A allocate 4 bytes as B deallocate A allocate 3 bytes as C allocate 1 byte as D deallocate B allocate 5 bytes as E deallocate C what is the largest remaining free segment of contiguous bytes after doing the allocations using BEST FIT? Solution: 8
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 19. (2 points) if you did Q16 using WORST FIT, what would be the largest remaining free segment of con-
  - **Correct**
    - tiguous bytes? Solution: 3
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 20. (2 points) assuming the free list is stored in address order, what would be the largest remaining free
  - **Correct**
    - segment of contiguous bytes if you did Q16 using FIRST FIT? Solution: 5
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 21. (2 points) find and explain the errors on two lines of this function.
  - **Correct**
    - // the return value should be freed by caller when finished char *tohex(int num) { char *hex_string; // line 1 if (num <0 || num > 9) { // line 2 return "can only handle numbers between 0 and 9"; // line 3 } char buffer[2]; // line 4 hex_string = buffer; // line 5 hex_string[0] 'O' + num; // line 6 hex_string[1] = '\0'; //line 7 return hex_string; // line 8
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 22. (2 points) Why is it impossible to implement LRU perfectly in the kernel?
  - **Correct**
    - No answer detected.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 23. (2 points) where can virtual memory pages that are not mapped into physical memory pages be stored?
  - **Correct**
    - No answer detected.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 24. (2 points) for the following questions assume we are using a cache of 3 physical pages and we get the
  - **Correct**
    - following sequence of virtual page accesses: 0121231414 how many cache hits will we get if we use FIFO page replacement? Solution: 4
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 25. (2 points) how many capacity cache misses will we get if we use FIFO page replacement?
  - **Correct**
    - Solution: 1
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 26. (2 points) how many compulsory cache misses will we get if we use LRU page replacement?
  - **Correct**
    - Solution: 5
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (2 points) 27. (2 points) Why are the page tables a sparse data structure?
  - **Correct**
    - No answer detected.
  - **Rubric**
    - Correct (+0)
    - Incorrect (-2)

- (10 points) 28. (2 points (bonus)) Which system calls are used by malloc() ?
  - **Correct**
    - 100 total points 2 bonus points
  - **Rubric**
    - Correct (+0)
    - Incorrect (-10)