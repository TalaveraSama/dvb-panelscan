#include "../tbsstream/newcamd.c"
#include <assert.h>
#include <signal.h>
#include <sys/wait.h>

static void init_pair(newcamd_t *a, newcamd_t *b, int protocol)
{
  int fds[2]; assert(socketpair(AF_UNIX,SOCK_STREAM,0,fds)==0);
  memset(a,0,sizeof(*a)); memset(b,0,sizeof(*b)); a->fd=fds[0]; b->fd=fds[1];
  a->protocol=b->protocol=protocol;
  uint8_t key[14]={1,2,3,4,5,6,7,8,9,10,11,12,13,14}, spread[16];
  des_key_spread(key,spread); des_set_keys(a,spread); des_set_keys(b,spread);
}

static void response(newcamd_t *server, uint16_t seq, int table, int len)
{
  uint8_t msg[35]={0}; msg[0]=table; msg[2]=len;
  memset(msg+3,0x55,len); server->seq=seq-1;
  assert(nc_send_msg(server,msg,3+len,123,0,0,NULL)==0);
}

static void test_protocol(int protocol)
{
  newcamd_t a,b; init_pair(&a,&b,protocol);
  uint8_t msg[]={0x80,0x70,5,1,2,3,4,5}, buf[1024], even[8], odd[8], zero[8]={0};
  uint16_t seq;
  assert(nc_send_msg(&a,msg,sizeof(msg),0x4567,0x1802,0x123456,&seq)==0);
  assert(nc_read_msg(&b,buf,sizeof(buf),500)==20); /* no padding in canonical length */
  assert(buf[4]==0x45 && buf[5]==0x67 && !memcmp(buf+12,msg,8));
  if (protocol==525) assert(buf[6]==0x18 && buf[7]==2 && buf[8]==0x12 && buf[10]==0x56);
  assert(newcamd_keepalive(&a)==0);
  assert(nc_read_msg(&b,buf,sizeof(buf),500)==15 && buf[12]==MSG_KEEPALIVE);
  /* Ignore both a stale sequence and wrong parity/table with the right seq. */
  uint16_t next=a.seq+1;
  response(&b,next-1,0x80,16); response(&b,next,0x81,16); response(&b,next,0x80,16);
  assert(newcamd_send_ecm(&a,123,0x1802,0,msg,sizeof(msg),even,odd,500)==0);
  assert(even[0]==0x55 && odd[7]==0x55);
  response(&b,a.seq+1,0x80,32);
  assert(newcamd_send_ecm(&a,123,0x1802,0,msg,sizeof(msg),even,odd,500)==NC_ECM_UNSUPPORTED);
  assert(!memcmp(even,zero,8) && !memcmp(odd,zero,8));
  msg[2]=6; /* malformed input rejected without reading/sending */
  assert(nc_send_msg(&a,msg,sizeof(msg),0,0,0,NULL)==-1);
  close(a.fd); close(b.fd);

  init_pair(&a,&b,protocol);
  int header=protocol==524 ? 8 : 12;
  memset(buf,0,sizeof(buf)); buf[header]=0x80; buf[header+2]=32;
  int size=des_encrypt(&b,buf,header+3); assert(size>0);
  buf[0]=(size-2)>>8; buf[1]=size-2;
  assert(send_full(b.fd,buf,size)==0);
  assert(nc_read_msg(&a,buf,sizeof(buf),500)==-1); /* cannot use DES padding as body */
  memset(buf,0,sizeof(buf)); buf[header]=0x80; buf[header+2]=16;
  size=des_encrypt(&b,buf,header+35); assert(size>0); /* excess body is NOT <=7 bytes padding */
  buf[0]=(size-2)>>8; buf[1]=size-2;
  assert(send_full(b.fd,buf,size)==0 && nc_read_msg(&a,buf,sizeof(buf),500)==-1);
  close(a.fd); close(b.fd);
}

static void test_card_bounds(void)
{
  newcamd_t nc={0}; newcamd_card_t card;
  uint8_t m[37]={0xe4,0,34,0,0x0b,0};
  m[14]=2; m[15]=0x12; m[16]=0x34; m[17]=0x56;
  m[26]=0xab; m[27]=0xcd; m[28]=0xef;
  assert(parse_card(&nc,m,sizeof(m))==0);
  newcamd_get_card(&nc,&card);
  assert(card.caid==0x0b00 && card.provider_count==2);
  assert(card.providers[0]==0x123456 && card.providers[1]==0xabcdef);
  assert(parse_card(&nc,m,36)==-1);
  assert(parse_card(&nc,m,14)==-1);
  m[14]=255; assert(parse_card(&nc,m,sizeof(m))==-1);
}

static void test_login(int protocol, int bad_card)
{
  int listener=socket(AF_INET,SOCK_STREAM,0); assert(listener>=0);
  struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
  assert(bind(listener,(struct sockaddr *)&addr,sizeof(addr))==0 && listen(listener,1)==0);
  socklen_t alen=sizeof(addr); assert(getsockname(listener,(struct sockaddr *)&addr,&alen)==0);
  uint8_t key[14]={1,2,3,4,5,6,7,8,9,10,11,12,13,14};
  pid_t child=fork(); assert(child>=0);
  if (!child) {
    newcamd_t server={.protocol=protocol};
    server.fd=accept(listener,NULL,NULL); assert(server.fd>=0); close(listener);
    uint8_t seed[14], mixed[14], spread[16], buf[1024];
    for (int i=0;i<14;i++) { seed[i]=i+15; mixed[i]=key[i]^seed[i]; }
    assert(send_full(server.fd,seed,14)==0);
    des_key_spread(mixed,spread); des_set_keys(&server,spread);
    char *salted=crypt_md5("test-password","$1$abcdefgh$"); assert(salted);
    int size=nc_read_msg(&server,buf,sizeof(buf),1000); assert(size>=15);
    assert(buf[12]==0xe0 && !strcmp((char *)buf+15,"test-user"));
    assert(!strcmp((char *)buf+15+strlen("test-user")+1,salted));
    uint8_t ack[3]={0xe1,0,0}; server.seq=((buf[2]<<8)|buf[3])-1;
    assert(nc_send_msg(&server,ack,3,0,0,0,NULL)==0);
    memcpy(mixed,key,14);
    for (size_t i=0;i<strlen(salted);i++) mixed[i%14]^=salted[i];
    free(salted); des_key_spread(mixed,spread); des_set_keys(&server,spread);
    assert(nc_read_msg(&server,buf,sizeof(buf),1000)==15 && buf[12]==0xe3);
    uint8_t card[26]={0xe4,0,23,0,0x0b,0}; card[14]=bad_card ? 2 : 1;
    card[15]=0x12; card[16]=0x34; card[17]=0x56;
    server.seq=((buf[2]<<8)|buf[3])-1;
    assert(nc_send_msg(&server,card,sizeof(card),0,0,0,NULL)==0);
    close(server.fd); _exit(0);
  }
  close(listener);
  char error[128]={0};
  newcamd_t *nc=newcamd_connect_proto("127.0.0.1",ntohs(addr.sin_port),key,
                              "test-user","test-password",protocol,error,sizeof(error));
  if (bad_card) assert(!nc && strstr(error,"datos de tarjeta"));
  else {
    assert(nc); newcamd_card_t card; newcamd_get_card(nc,&card);
    assert(card.caid==0x0b00 && card.provider_count==1 && card.providers[0]==0x123456);
    newcamd_close(nc);
  }
  int status; assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
}

static void test_stale_responses_cannot_extend_deadline(void)
{
  newcamd_t a,b; init_pair(&a,&b,525);
  pid_t child=fork(); assert(child>=0);
  if (!child) {
    close(a.fd);
    uint8_t cw[19]={0x80,0,16};
    for (int i=0;i<80;i++) {
      b.seq=9998;
      if (nc_send_msg(&b,cw,sizeof(cw),0,0,0,NULL)<0) break;
      usleep(10000);
    }
    close(b.fd); _exit(0);
  }
  close(b.fd);
  uint8_t msg[]={0x80,0,0}, even[8], odd[8];
  uint64_t before=nc_now_ms();
  assert(newcamd_send_ecm(&a,1,0x1802,0,msg,3,even,odd,40)==-1);
  assert(nc_now_ms()-before<300);
  close(a.fd); kill(child,SIGTERM); int status; assert(waitpid(child,&status,0)==child);
}

int main(void) {
  signal(SIGPIPE,SIG_IGN);
  test_protocol(525); test_protocol(524); test_card_bounds();
  test_login(525,0); test_login(524,0); test_login(525,1);
  test_stale_responses_cannot_extend_deadline();
  /* Published md5crypt vector, independent of framing roundtrip. */
  char *hash = crypt_md5("password", "$1$saltqwer$");
  assert(!strcmp(hash, "$1$saltqwer$yCutmodwBoXKLyFtgW5r31"));
  free(hash);
  int sockets[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  newcamd_t a = {.fd=sockets[0]}, b = {.fd=sockets[1]};
  uint8_t key[14] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14}, spread[16];
  des_key_spread(key, spread);
  des_set_keys(&a, spread); des_set_keys(&b, spread);
  uint8_t message[] = {0x80,0x70,5,1,2,3,4,5}, buf[1024];
  uint16_t seq;
  assert(nc_send_msg(&a, message, sizeof(message), 123, 0x100, 0, &seq) == 0);
  assert(nc_read_msg(&b, buf, sizeof(buf), 500) >= 20);
  assert(!memcmp(buf+12, message, sizeof(message)));
  assert(buf[4] == 0 && buf[5] == 123);
  assert(((buf[2]<<8)|buf[3]) == seq);
  assert(nc_send_msg(&a, message, 1024, 0, 0, 0, NULL) == -1);
  /* A valid encrypted short response must not be accepted as CW padding. */
  uint8_t denied[] = {0x80,0,0};
  b.seq = a.seq; /* next ECM uses the next sequence */
  assert(nc_send_msg(&b, denied, 3, 0, 0, 0, NULL) == 0);
  uint8_t even[8], odd[8];
  assert(newcamd_send_ecm(&a, 123, 0x100, 0, message, sizeof(message), even, odd, 500) == -2);
  close(sockets[0]); close(sockets[1]);
  puts("Newcamd framing tests passed");
  return 0;
}
