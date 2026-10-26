#define main scanner_main
#include "../dvb2m3u8/dvb2m3u8.c"
#undef main
#include <assert.h>

static uint8_t to_bcd(int n) { return (n/10)*16+n%10; }
int main(void) {
  uint8_t descriptors[]={0x4d,13,'s','p','a',4,'S','h','o','w',4,'T','e','s','t'};
  uint8_t body[11+12+sizeof(descriptors)]={2,0xa0,0xc1,0,0,0,42,0,1,0,0x50};
  time_t future=time(NULL)+3600;
  struct tm *tm=gmtime(&future);
  uint8_t *event=body+11;
  int mjd=future/86400+40587;
  event[1]=1;event[2]=mjd>>8;event[3]=mjd;
  event[4]=to_bcd(tm->tm_hour);event[5]=to_bcd(tm->tm_min);event[6]=to_bcd(tm->tm_sec);
  event[7]=1;event[8]=0x30;event[10]=0xf0;event[11]=sizeof(descriptors);
  memcpy(event+12,descriptors,sizeof(descriptors));
  current_tsid=42;service_t *service=service_add(672);
  parse_eit(0x50,body,sizeof(body));
  assert(service->event_count==1 && service->events->start==future);
  assert(service->events->stop==future+5400);
  assert(!strcmp(service->events->title,"Show"));
  assert(!strcmp(service->events->description,"Test"));
  parse_eit(0x50,body,sizeof(body));assert(service->event_count==1);
  event[1]=2;parse_eit(0x60,body,sizeof(body));assert(service->event_count==1);
  body[6]=43;parse_eit(0x50,body,sizeof(body));assert(service->event_count==1);
  body[6]=42;event[4]=0xff;parse_eit(0x50,body,sizeof(body));assert(service->event_count==1);
  /* Full 4096-byte EIT, including last-packet stuffing, must reassemble. */
  uint8_t large[4096]={0x50,0xff,0xfd};
  event[1]=7; event[4]=to_bcd(tm->tm_hour);
  memcpy(large+3,body,11+12);
  large[24]=0xff;large[25]=0xe2;
  int at=26;
  memcpy(large+at,descriptors,sizeof(descriptors));at+=sizeof(descriptors);
  while (at<4092) {
    int length=4092-at-2; if (length>254) length=254;
    large[at++]=0x80; large[at++]=length;
    memset(large+at,0,length); at+=length;
  }
  uint32_t crc=crc32_mpeg(large,4092);
  large[4092]=crc>>24;large[4093]=crc>>16;large[4094]=crc>>8;large[4095]=crc;
  for (int pos=0;pos<4096;) {
    uint8_t payload[184];memset(payload,0xff,sizeof(payload));
    int first=pos==0, take=first ? 183 : 184;
    if (take>4096-pos) take=4096-pos;
    if (first) payload[0]=0;
    memcpy(payload+first,large+pos,take);pos+=take;
    feed_section(PID_EIT,payload,sizeof(payload),first);
  }
  assert(service->event_count==2);
  assert(!strcmp(service->events->title,"Show"));
  FILE *out=tmpfile();assert(out);opts_t opts={0};emit_json(&opts,out);rewind(out);
  char json[4096]={0};assert(fread(json,1,sizeof(json)-1,out)>0);fclose(out);
  assert(strstr(json,"\"events\": ["));assert(strstr(json,"\"start\":"));
  while(service->events) { epg_event_t *next=service->events->next;free(service->events);service->events=next; }
  free(service);services=NULL;
  puts("Scanner EIT: real UTC times, schedule, TS isolation, BCD and JSON passed");
  return 0;
}
